"""連線檢查：一鍵測試所有外部串接（交易所、AI、資料來源），只讀取、不下任何單。

每一項回傳 ok / fail / warn / skip 與說明；彼此平行執行，單項逾時不影響其他項目。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel
from sqlmodel import Session, select

from goldhunter.ai.base import AIProviderError
from goldhunter.core.models import Instrument
from goldhunter.core.secrets import decrypt
from goldhunter.exchanges.ccxt_adapter import SUPPORTED, CCXTExchange, exchange_symbol
from goldhunter.intel import sources
from goldhunter.intel.hub import load_settings
from goldhunter.store.db import AIModelConfig, Bot, ExchangeAccount

TIMEOUT_SEC = 25
BTC = Instrument.parse("crypto:BTC/USDT:perp")
GOLD_BASES = ("PAXG", "XAUT", "XAU", "GOLD")


class CheckItem(BaseModel):
    name: str
    status: str  # ok / fail / warn / skip
    detail: str = ""
    ms: int | None = None


class CheckGroup(BaseModel):
    key: str
    label: str
    note: str = ""
    items: list[CheckItem]


class Outcome(Exception):
    """在檢查函式裡丟出，直接指定狀態（例如 warn / skip）"""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


async def _run(name: str, fn: Callable[[], Awaitable[str]]) -> CheckItem:
    t = time.perf_counter()
    try:
        detail = await asyncio.wait_for(fn(), TIMEOUT_SEC)
        status = "ok"
    except Outcome as o:
        status, detail = o.status, o.detail
    except TimeoutError:
        status, detail = "fail", f"逾時（超過 {TIMEOUT_SEC} 秒沒有回應）"
    except Exception as e:  # 顯示真正的錯誤，方便排查
        status, detail = "fail", _err(e)
    return CheckItem(name=name, status=status, detail=detail, ms=round((time.perf_counter() - t) * 1000))


HINTS = {
    "AuthenticationError": "API Key / Secret（OKX 另需 Passphrase）錯誤，或 Key 已刪除",
    "PermissionDenied": "API Key 權限不足（需要「讀取」與「合約交易」），或這台主機的 IP 不在交易所的 IP 白名單",
    "AccountSuspended": "帳戶被交易所限制",
    "ExchangeNotAvailable": "連不到交易所：主機所在地區可能被封鎖（例如美國 IP 連 Binance / Bybit），或防火牆擋住",
    "NetworkError": "網路錯誤：主機連不到交易所，請檢查網路或防火牆",
    "RequestTimeout": "交易所回應太慢，稍後再試",
    "DDoSProtection": "被交易所限流，稍後再試",
    "InvalidNonce": "主機時間不準，請校正系統時間（NTP）",
}


def _err(e: Exception) -> str:
    msg = str(e).strip().replace("\n", " ")
    base = f"{type(e).__name__}: {msg[:240]}" if msg else type(e).__name__
    hint = next((h for cls in type(e).__mro__ for k, h in HINTS.items() if cls.__name__ == k), None)
    return f"{hint}（{base}）" if hint else base


def _fmt_price(p: float) -> str:
    return f"{p:,.2f}" if p >= 1 else f"{p:.6g}"


# ------------------------------ 交易所公開行情 ------------------------------
async def _public_exchange(eid: str) -> list[CheckItem]:
    ex = CCXTExchange(eid)
    swaps: list[dict] = []

    async def markets() -> str:
        await ex._ensure_markets()
        quote = "USDC" if eid == "hyperliquid" else "USDT"
        swaps.extend(m for m in ex.client.markets.values() if m.get("swap") and m.get("settle") == quote)
        if not swaps:
            raise Outcome("fail", "讀到市場清單，但沒有 USDT 永續合約")
        return f"{len(swaps)} 個 {quote} 永續合約"

    async def price() -> str:
        return f"BTC 最新價 {_fmt_price(await ex.fetch_price(BTC))}"

    async def gold() -> str:
        if not swaps:
            raise Outcome("skip", "市場清單讀取失敗，無法檢查")
        found = sorted({m.get("base") for m in swaps if (m.get("base") or "").upper() in GOLD_BASES})
        if not found:
            raise Outcome("warn", "沒有黃金永續合約（PAXG / XAUT），MRSPENCER 無法在這家交易所跑")
        return "有：" + "、".join(f"{b}（crypto:{b}/USDT:perp）" for b in found)

    try:
        items = [await _run("市場清單", markets), await _run("即時價格", price), await _run("黃金合約", gold)]
    finally:
        await ex.close()
    return items


# ------------------------------ 交易所帳戶 ------------------------------
async def _account(acc: ExchangeAccount, bot_symbols: list[str]) -> CheckGroup:
    label = f"{acc.name}（{SUPPORTED.get(acc.exchange_id, {}).get('label', acc.exchange_id)}，" \
            f"{'模擬' if acc.paper else '實盤'}{'・測試網' if acc.testnet else ''}）"
    if acc.paper:
        return CheckGroup(key=f"acc-{acc.id}", label=label, items=[CheckItem(
            name="模擬帳戶", status="ok",
            detail=f"不需要 API Key；行情用 {acc.exchange_id} 公開資料（見上方「交易所公開行情」），成交在平台內模擬")])
    ex = CCXTExchange(acc.exchange_id, api_key=decrypt(acc.api_key_enc), secret=decrypt(acc.secret_enc),
                      passphrase=decrypt(acc.passphrase_enc), testnet=acc.testnet)

    async def balance() -> str:
        b = await ex.fetch_balance()
        if b.total <= 0:
            raise Outcome("warn", "API Key 可用，但 USDT 餘額為 0（資金可能在現貨或其他帳戶）")
        return f"權益 {b.total:,.2f} USDT，可用 {b.free:,.2f}"

    async def positions() -> str:
        ps = await ex.fetch_positions()
        return f"目前 {len(ps)} 個持倉" + ("：" + "、".join(p.instrument.symbol for p in ps[:6]) if ps else "")

    async def symbols() -> str:
        if not bot_symbols:
            raise Outcome("skip", "沒有 Bot 使用這個帳戶的固定交易對")
        await ex._ensure_markets()
        missing = [s for s in bot_symbols if exchange_symbol(acc.exchange_id, Instrument.parse(s)) not in ex.client.markets]
        if missing:
            raise Outcome("fail", "交易所沒有這些合約：" + "、".join(Instrument.parse(s).symbol for s in missing))
        return f"{len(bot_symbols)} 個交易對都有上架"

    async def stop_orders() -> str:
        return "支援：開倉後會在交易所掛只減倉的條件止損單（實際掛單要等第一次開倉才會確認）"

    try:
        items = [await _run("API Key / 餘額", balance), await _run("持倉查詢", positions),
                 await _run("Bot 交易對", symbols), await _run("交易所止損單", stop_orders)]
    finally:
        await ex.close()
    return CheckGroup(key=f"acc-{acc.id}", label=label, note="只讀取餘額與持倉，不會下單", items=items)


# ------------------------------ AI 模型 ------------------------------
async def _ai(m: AIModelConfig) -> CheckItem:
    from goldhunter.engine.manager import provider_from_config

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}

    async def call() -> str:
        try:
            r = await provider_from_config(m).complete_json("你是連線測試。", '回傳 {"ok": true}', schema)
        except AIProviderError as e:
            raise Outcome("fail", str(e)[:300]) from e
        return f"回覆正常（{r.model}，{r.input_tokens + r.output_tokens} tokens）"

    return await _run(f"{m.name}（{m.provider}{' · ' + m.model if m.model else ''}）", call)


# ------------------------------ 資料來源 ------------------------------
async def _get(url: str, **params) -> httpx.Response:
    async with httpx.AsyncClient(timeout=sources.TIMEOUT, headers=sources.UA, follow_redirects=True) as c:
        r = await c.get(url, params=params or None)
        r.raise_for_status()
        return r


async def _sources() -> list[CheckItem]:
    st = load_settings()
    checks: list[tuple[str, Callable[[], Awaitable[str]]]] = []

    async def fear_greed() -> str:
        d = (await _get("https://api.alternative.me/fng/", limit=1)).json()["data"][0]
        return f"目前 {d['value']}（{d['value_classification']}）"

    async def calendar() -> str:
        rows = (await _get(sources.CALENDAR_URL)).json()
        high = [r for r in rows if r.get("country") == "USD" and r.get("impact") == "High"]
        return f"本週 {len(rows)} 個事件，美國高影響 {len(high)} 個"

    async def derivatives() -> str:
        d = await sources.derivatives("binance", BTC)
        if not d:
            raise Outcome("fail", "沒有取得資金費率 / 未平倉量")
        parts = []
        if "funding_rate_pct" in d:
            parts.append(f"BTC 資金費率 {d['funding_rate_pct']}%")
        if "open_interest" in d:
            parts.append(f"未平倉 {d['open_interest']:,.0f}")
        return "、".join(parts) or "有回應"

    def rss(url: str) -> Callable[[], Awaitable[str]]:
        async def f() -> str:
            items = sources._parse_rss((await _get(url)).text, httpx.URL(url).host)
            if not items:
                raise Outcome("warn", "連得上，但讀不到新聞標題（格式可能改了）")
            return f"{len(items)} 則，最新：{items[0]['title'][:40]}"
        return f

    async def fred() -> str:
        key = decrypt(st.fred_api_key_enc)
        if not key:
            raise Outcome("skip", "未設定 FRED API Key（選填，免費申請）；沒有的話 AI 看不到總經數據")
        r = await _get("https://api.stlouisfed.org/fred/series/observations", series_id="FEDFUNDS",
                       api_key=key, file_type="json", sort_order="desc", limit=1)
        o = r.json()["observations"][0]
        return f"聯邦基金利率 {o['value']}%（{o['date']}）"

    async def cryptopanic() -> str:
        token = decrypt(st.cryptopanic_token_enc)
        if not token:
            raise Outcome("skip", "未設定 CryptoPanic Token（選填）；RSS 新聞仍可使用")
        r = await _get("https://cryptopanic.com/api/developer/v2/posts/", auth_token=token, public="true")
        return f"{len(r.json().get('results', []))} 則新聞"

    checks.append(("恐懼貪婪指數", fear_greed))
    checks.append(("經濟日曆", calendar))
    checks.append(("資金費率 / 未平倉（Binance）", derivatives))
    for url in st.rss_urls or sources.DEFAULT_RSS:
        checks.append((f"新聞 RSS：{httpx.URL(url).host}", rss(url)))
    checks.append(("總經數據（FRED）", fred))
    checks.append(("CryptoPanic 新聞", cryptopanic))
    return list(await asyncio.gather(*(_run(n, f) for n, f in checks)))


# ------------------------------ 總入口 ------------------------------
async def run_all(session: Session, include_ai: bool = True) -> dict:
    t0 = time.perf_counter()
    accounts = list(session.exec(select(ExchangeAccount)))
    models = list(session.exec(select(AIModelConfig)))
    bot_syms: dict[int, list[str]] = {}
    for b in session.exec(select(Bot)):
        bot_syms.setdefault(b.account_id, []).extend(b.symbols or [])

    async def public_group() -> CheckGroup:
        results = await asyncio.gather(*(_public_exchange(eid) for eid in SUPPORTED))
        items = [CheckItem(name=f"{SUPPORTED[eid]['label']}・{it.name}", status=it.status, detail=it.detail, ms=it.ms)
                 for eid, group in zip(SUPPORTED, results, strict=True) for it in group]
        return CheckGroup(key="public", label="交易所公開行情", note="不需要 API Key；回測與模擬帳戶都用這裡的資料", items=items)

    async def ai_group() -> CheckGroup:
        if not models:
            items = [CheckItem(name="AI 模型", status="skip", detail="還沒有設定 AI 模型")]
        elif not include_ai:
            items = [CheckItem(name=m.name, status="skip", detail="這次沒有勾選檢查 AI") for m in models]
        else:
            items = list(await asyncio.gather(*(_ai(m) for m in models)))
        return CheckGroup(key="ai", label="AI 模型", note="每個模型送一次極短的測試請求（費用極少）", items=items)

    async def sources_group() -> CheckGroup:
        return CheckGroup(key="sources", label="市場情報資料來源", note="新聞、總經、情緒與合約數據", items=await _sources())

    groups = await asyncio.gather(
        public_group(),
        *(_account(a, sorted(set(bot_syms.get(a.id or 0, [])))) for a in accounts),
        ai_group(), sources_group())
    all_items = [i for g in groups for i in g.items]
    summary = {k: sum(1 for i in all_items if i.status == k) for k in ("ok", "warn", "fail", "skip")}
    return {"checked_at": datetime.now(UTC).isoformat(), "duration_ms": round((time.perf_counter() - t0) * 1000),
            "summary": summary, "groups": [g.model_dump() for g in groups]}
