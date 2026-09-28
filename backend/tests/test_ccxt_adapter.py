"""用假的 ccxt client 驗證實盤下單參數（不連網）"""

from goldhunter.core.models import OrderRequest, OrderStatus, Side
from goldhunter.exchanges.ccxt_adapter import CCXTExchange

from .conftest import BTC_PERP


class FakeClient:
    def __init__(self):
        self.calls = []
        self.has = {"setLeverage": True, "fetchPositions": True}
        self.markets = {}

    async def load_markets(self):
        return {}

    async def set_leverage(self, lev, sym):
        self.calls.append(("set_leverage", lev, sym))

    async def create_order(self, sym, typ, side, qty, price, params):
        self.calls.append(("create_order", sym, typ, side, qty, price, params))
        return {"id": "1", "status": "closed", "filled": qty, "average": 100.0, "fee": {"cost": 0.05}}

    async def fetch_ticker(self, sym):
        return {"last": 100.0}

    async def fetch_positions(self):
        return [{"symbol": "BTC/USDC:USDC", "contracts": 2, "contractSize": 1, "side": "short",
                 "entryPrice": 101, "leverage": 3, "unrealizedPnl": 2}]

    async def close(self):
        pass


async def test_place_order_perp_params():
    ex = CCXTExchange("binance", api_key="k", secret="s")
    ex.client = FakeClient()
    o = await ex.place_order(OrderRequest(instrument=BTC_PERP, side=Side.SELL, quantity=0.5, leverage=3,
                                          reduce_only=True))
    assert o.status == OrderStatus.FILLED and o.avg_price == 100.0 and o.fee == 0.05
    assert ("set_leverage", 3, "BTC/USDT:USDT") in ex.client.calls
    create = next(c for c in ex.client.calls if c[0] == "create_order")
    assert create[1:5] == ("BTC/USDT:USDT", "market", "sell", 0.5) and create[6] == {"reduceOnly": True}


async def test_hyperliquid_market_order_uses_usdc_and_price():
    ex = CCXTExchange("hyperliquid", api_key="0xabc", secret="0xkey")
    ex.client = FakeClient()
    await ex.place_order(OrderRequest(instrument=BTC_PERP, side=Side.BUY, quantity=1))
    create = next(c for c in ex.client.calls if c[0] == "create_order")
    assert create[1] == "BTC/USDC:USDC" and create[5] == 100.0  # 市價單帶參考價
    pos = await ex.fetch_positions()
    assert pos[0].instrument == BTC_PERP and pos[0].quantity == -2
