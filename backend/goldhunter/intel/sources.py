"""外部市場情報來源：合約數據、恐懼貪婪指數、新聞、總經數據、經濟日曆。

每個來源都「盡力而為」：失敗時回傳 None / 空清單並記錄錯誤，不會讓交易流程中斷。
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import ccxt.async_support as ccxt
import httpx

from goldhunter.core.models import Instrument
from goldhunter.exchanges.ccxt_adapter import exchange_symbol

log = logging.getLogger("goldhunter.intel")

TIMEOUT = httpx.Timeout(10.0)
UA = {"User-Agent": "GoldHunter/0.1"}

DEFAULT_RSS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]
FRED_SERIES = {
    "CPIAUCSL": "美國 CPI 指數",
    "FEDFUNDS": "聯邦基金利率 %",
    "DGS10": "美國 10 年期公債殖利率 %",
    "DTWEXBGS": "美元指數（廣義）",
    "UNRATE": "美國失業率 %",
}
# 社群常用的免費經濟日曆 JSON（非官方來源，失敗時自動略過）
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


async def derivatives(exchange_id: str, inst: Instrument) -> dict:
    """資金費率、未平倉量（公開資料，不需 API Key）"""
    client: ccxt.Exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    out: dict = {}
    try:
        await client.load_markets()
        sym = exchange_symbol(exchange_id, inst)
        try:
            fr = await client.fetch_funding_rate(sym)
            out["funding_rate_pct"] = round(float(fr.get("fundingRate") or 0) * 100, 5)
            if fr.get("fundingDatetime"):
                out["next_funding"] = fr["fundingDatetime"]
        except Exception as e:
            log.debug("funding rate 失敗：%s", e)
        try:
            oi = await client.fetch_open_interest(sym)
            val = oi.get("openInterestValue") or oi.get("openInterestAmount")
            if val:
                out["open_interest"] = float(val)
        except Exception as e:
            log.debug("open interest 失敗：%s", e)
        if client.has.get("fetchLongShortRatioHistory"):
            try:
                rows = await client.fetch_long_short_ratio_history(sym, "1h", limit=1)
                if rows:
                    out["long_short_ratio"] = rows[-1].get("longShortRatio")
            except Exception as e:
                log.debug("long/short ratio 失敗：%s", e)
    except Exception as e:
        log.warning("合約數據取得失敗：%s", e)
    finally:
        await client.close()
    return out


async def fear_greed() -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA) as c:
            r = await c.get("https://api.alternative.me/fng/", params={"limit": 2})
            r.raise_for_status()
            data = r.json()["data"]
        now, prev = data[0], data[1] if len(data) > 1 else None
        return {"value": int(now["value"]), "label": now["value_classification"],
                "yesterday": int(prev["value"]) if prev else None}
    except Exception as e:
        log.warning("恐懼貪婪指數取得失敗：%s", e)
        return None


def _parse_rss(xml_text: str, source: str) -> list[dict]:
    items = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        ts = None
        if pub := it.findtext("pubDate"):
            try:
                ts = parsedate_to_datetime(pub).astimezone(UTC)
            except (TypeError, ValueError):
                ts = None
        items.append({"title": title, "source": source, "published": ts.isoformat() if ts else None,
                      "url": (it.findtext("link") or "").strip()})
    return items


async def news(keywords: list[str], rss_urls: list[str] | None = None, cryptopanic_token: str | None = None,
               limit: int = 12) -> list[dict]:
    """新聞標題：RSS（免費）＋ CryptoPanic（選填 token）。依關鍵字篩選，沒有命中時回傳最新的通用新聞。"""
    items: list[dict] = []
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA, follow_redirects=True) as c:
        for url in rss_urls or DEFAULT_RSS:
            try:
                r = await c.get(url)
                r.raise_for_status()
                items += _parse_rss(r.text, httpx.URL(url).host)
            except Exception as e:
                log.warning("RSS %s 失敗：%s", url, e)
        if cryptopanic_token:
            try:
                r = await c.get("https://cryptopanic.com/api/developer/v2/posts/",
                                params={"auth_token": cryptopanic_token, "public": "true",
                                        "currencies": ",".join(k.upper() for k in keywords[:3])})
                r.raise_for_status()
                for p in r.json().get("results", []):
                    items.append({"title": p.get("title", ""), "source": "cryptopanic",
                                  "published": p.get("published_at"), "url": p.get("url") or ""})
            except Exception as e:
                log.warning("CryptoPanic 失敗：%s", e)
    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    kws = [k.lower() for k in keywords if k]
    hit = [i for i in items if any(k in i["title"].lower() for k in kws)]
    rest = [i for i in items if i not in hit]
    return (hit + rest)[:limit]


async def macro(fred_api_key: str | None) -> dict:
    """FRED 總經數據（需要免費 API Key：https://fred.stlouisfed.org/docs/api/api_key.html）"""
    if not fred_api_key:
        return {}
    out: dict = {}
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA) as c:
        for sid, label in FRED_SERIES.items():
            try:
                r = await c.get("https://api.stlouisfed.org/fred/series/observations", params={
                    "series_id": sid, "api_key": fred_api_key, "file_type": "json", "sort_order": "desc", "limit": 13})
                r.raise_for_status()
                obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", None)]
                if not obs:
                    continue
                latest = float(obs[0]["value"])
                entry = {"label": label, "date": obs[0]["date"], "value": latest}
                if len(obs) > 1:
                    entry["previous"] = float(obs[1]["value"])
                if sid == "CPIAUCSL" and len(obs) >= 13:
                    entry["yoy_pct"] = round((latest / float(obs[12]["value"]) - 1) * 100, 2)
                out[sid] = entry
            except Exception as e:
                log.warning("FRED %s 失敗：%s", sid, e)
    return out


async def economic_calendar(countries: tuple[str, ...] = ("USD",)) -> list[dict]:
    """本週高影響力經濟事件（CPI、非農、FOMC 等）"""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=UA) as c:
            r = await c.get(CALENDAR_URL)
            r.raise_for_status()
            rows = r.json()
    except Exception as e:
        log.warning("經濟日曆取得失敗：%s", e)
        return []
    out = []
    for row in rows:
        if row.get("country") not in countries or row.get("impact") != "High":
            continue
        try:
            ts = datetime.fromisoformat(row["date"]).astimezone(UTC)
        except (KeyError, ValueError):
            continue
        out.append({"title": row.get("title"), "time": ts.isoformat(), "country": row.get("country"),
                    "forecast": row.get("forecast"), "previous": row.get("previous")})
    return out
