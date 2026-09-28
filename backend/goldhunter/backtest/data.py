"""歷史 K 線下載（透過 ccxt 公開 API，不需要 API Key）。"""

from __future__ import annotations

import ccxt.async_support as ccxt

from goldhunter.core.models import Candle, Instrument
from goldhunter.exchanges.ccxt_adapter import exchange_symbol

MAX_BARS = 20_000


async def fetch_history(exchange_id: str, instrument: Instrument, timeframe: str, start_ms: int,
                        end_ms: int) -> list[Candle]:
    client: ccxt.Exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    try:
        await client.load_markets()
        tf_ms = client.parse_timeframe(timeframe) * 1000
        out: list[Candle] = []
        since = start_ms
        while since < end_ms and len(out) < MAX_BARS:
            rows = await client.fetch_ohlcv(exchange_symbol(exchange_id, instrument), timeframe, since=since, limit=1000)
            if not rows:
                break
            for r in rows:
                if r[0] >= end_ms:
                    break
                if not out or r[0] > out[-1].ts:
                    out.append(Candle(ts=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5] or 0))
            next_since = rows[-1][0] + tf_ms
            if next_since <= since:
                break
            since = next_since
        return out
    finally:
        await client.close()
