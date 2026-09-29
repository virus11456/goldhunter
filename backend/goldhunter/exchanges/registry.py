from __future__ import annotations

from goldhunter.exchanges.base import ExchangeAdapter
from goldhunter.exchanges.ccxt_adapter import SUPPORTED, CCXTExchange
from goldhunter.exchanges.paper import PaperExchange


def available_exchanges() -> list[dict]:
    items = [{"id": k, "market": "crypto", **v} for k, v in SUPPORTED.items()]
    # 預留：美股 / 台股
    items += [
        {"id": "alpaca", "market": "us", "label": "Alpaca（美股，規劃中）", "needs_passphrase": False, "planned": True},
        {"id": "shioaji", "market": "tw", "label": "永豐 Shioaji（台股，規劃中）", "needs_passphrase": False, "planned": True},
    ]
    return items


def build_exchange(
    exchange_id: str,
    *,
    api_key: str | None = None,
    secret: str | None = None,
    passphrase: str | None = None,
    testnet: bool = False,
    paper: bool = False,
    paper_cash: float = 10_000.0,
) -> ExchangeAdapter:
    """建立交易所連線。paper=True 時使用該交易所的公開行情 + 模擬成交，不需要 API Key。"""
    if paper:
        market_data = CCXTExchange(exchange_id, testnet=False)

        async def source(inst, tf, limit):
            return await market_data.fetch_candles(inst, tf, limit)

        ex = PaperExchange(initial_cash=paper_cash, candle_source=source)
        ex.id = f"paper:{exchange_id}"
        ex._market_data = market_data  # type: ignore[attr-defined]  # 供 close() 釋放
        orig_close = ex.close

        async def close():
            await market_data.close()
            await orig_close()

        ex.close = close  # type: ignore[method-assign]
        ex.perp_volumes = market_data.perp_volumes  # type: ignore[method-assign]
        return ex
    return CCXTExchange(exchange_id, api_key=api_key, secret=secret, passphrase=passphrase, testnet=testnet)
