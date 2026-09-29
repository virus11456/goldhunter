"""從 Hyperliquid 公開 API 取得錢包的真實成交紀錄並彙整成交易習慣摘要（鏈上資料公開、不需要金鑰）。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

import httpx

HL_INFO = "https://api.hyperliquid.xyz/info"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


async def fetch_fills(address: str, client: httpx.AsyncClient | None = None) -> list[dict]:
    if not ADDRESS_RE.match(address):
        raise ValueError("錢包地址格式錯誤（應為 0x 開頭的 42 碼）")
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    try:
        r = await client.post(HL_INFO, json={"type": "userFills", "user": address})
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    finally:
        if own:
            await client.aclose()


def summarize_fills(fills: list[dict]) -> dict:
    """把成交明細整理成交易習慣統計（給 AI 蒸餾時當一手資料）"""
    if not fills:
        return {"fills": 0}
    fills = sorted(fills, key=lambda f: f.get("time", 0))
    coins = Counter(f.get("coin") for f in fills)
    opens = [f for f in fills if str(f.get("dir", "")).startswith("Open")]
    closes = [f for f in fills if str(f.get("dir", "")).startswith("Close")]
    longs = sum(1 for f in opens if "Long" in str(f.get("dir")))
    pnls = [float(f.get("closedPnl") or 0) for f in closes if float(f.get("closedPnl") or 0) != 0]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    notional = [float(f.get("px", 0)) * float(f.get("sz", 0)) for f in fills]
    # 持倉時間：同一幣種從第一筆 Open 到部位歸零的 Close
    first_open: dict[str, int] = {}
    holds: list[float] = []
    for f in fills:
        c, d = f.get("coin"), str(f.get("dir", ""))
        if d.startswith("Open") and c not in first_open:
            first_open[c] = f.get("time", 0)
        elif d.startswith("Close") and c in first_open and float(f.get("startPosition", 0) or 0) != 0:
            start = abs(float(f.get("startPosition") or 0))
            if abs(start - float(f.get("sz") or 0)) < 1e-9:  # 這筆把部位平光
                holds.append((f.get("time", 0) - first_open.pop(c)) / 3_600_000)
    by_hour = Counter(datetime.fromtimestamp(f.get("time", 0) / 1000, tz=UTC).hour for f in opens)
    daily = defaultdict(int)
    for f in opens:
        daily[datetime.fromtimestamp(f.get("time", 0) / 1000, tz=UTC).date()] += 1
    span_days = max(1.0, (fills[-1].get("time", 0) - fills[0].get("time", 0)) / 86_400_000)
    return {
        "fills": len(fills),
        "period_days": round(span_days, 1),
        "top_coins": coins.most_common(8),
        "open_trades": len(opens),
        "long_ratio_pct": round(longs / len(opens) * 100, 1) if opens else None,
        "trades_per_day": round(len(opens) / span_days, 2),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "total_closed_pnl": round(sum(pnls), 2),
        "median_notional_usd": round(sorted(notional)[len(notional) // 2], 2),
        "avg_hold_hours": round(sum(holds) / len(holds), 1) if holds else None,
        "most_active_utc_hours": [h for h, _ in by_hour.most_common(3)],
    }


def summary_to_corpus(address: str, s: dict) -> str:
    if not s.get("fills"):
        return f"Hyperliquid 錢包 {address}：查無成交紀錄。"
    coins = "、".join(f"{c}（{n} 筆）" for c, n in s["top_coins"])
    return (
        f"## 鏈上真實交易紀錄（Hyperliquid 錢包 {address}，最近 {s['fills']} 筆成交、約 {s['period_days']} 天）\n"
        f"- 常交易：{coins}\n"
        f"- 開倉 {s['open_trades']} 次，做多比例 {s['long_ratio_pct']}%，平均每天開倉 {s['trades_per_day']} 次\n"
        f"- 勝率 {s['win_rate_pct']}%，平均獲利 {s['avg_win']}、平均虧損 {s['avg_loss']}，合計已實現 {s['total_closed_pnl']} USD\n"
        f"- 單筆成交中位數約 {s['median_notional_usd']} USD，平均持倉 {s['avg_hold_hours']} 小時\n"
        f"- 最常開倉的時段（UTC）：{s['most_active_utc_hours']}\n"
        "請把這些實際行為當作最重要的證據：他實際怎麼做，比他公開說什麼更可信。"
    )
