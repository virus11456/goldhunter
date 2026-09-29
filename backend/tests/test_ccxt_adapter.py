"""用假的 ccxt client 驗證實盤下單參數（不連網）"""
import pytest

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


async def test_contract_size_conversion():
    """OKX 等以「張」下單：1 張 = contractSize 個幣，下單要換算，成交量要換回幣"""
    ex = CCXTExchange("okx", api_key="k", secret="s", passphrase="p")
    ex.client = FakeClient()
    ex.client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01, "limits": {"amount": {"min": 1}}}}
    ex.client.amount_to_precision = lambda sym, amt: f"{int(amt)}"
    ex._markets_loaded = True
    assert abs(ex.round_qty(BTC_PERP, 0.257) - 0.25) < 1e-12
    assert abs(ex.capabilities(BTC_PERP).min_qty - 0.01) < 1e-12
    o = await ex.place_order(OrderRequest(instrument=BTC_PERP, side=Side.BUY, quantity=0.25))
    create = next(c for c in ex.client.calls if c[0] == "create_order")
    assert abs(create[4] - 25) < 1e-9 and abs(o.filled - 0.25) < 1e-12


async def test_stop_order_params_per_exchange():
    import ccxt.async_support as ccxt

    ex = CCXTExchange("binance", api_key="k", secret="s")
    ex.client = FakeClient()
    oid = await ex.place_stop_order(BTC_PERP, Side.SELL, 0.5, 95.0)
    create = next(c for c in ex.client.calls if c[0] == "create_order")
    assert oid == "1" and create[1:6] == ("BTC/USDT:USDT", "market", "sell", 0.5, None)
    assert create[6] == {"stopLossPrice": 95.0, "reduceOnly": True}

    cancels = []

    async def cancel(oid, sym, params=None):
        cancels.append((oid, sym, params))

    ex.client.cancel_order = cancel
    await ex.cancel_stop_order(BTC_PERP, "9")
    assert cancels == [("9", "BTC/USDT:USDT", {"trigger": True})]  # Binance 條件單走 algo API

    async def not_found(oid, sym, params=None):
        raise ccxt.OrderNotFound("gone")

    ex.client.cancel_order = not_found
    await ex.cancel_stop_order(BTC_PERP, "9")  # 已觸發 / 已取消：不算錯誤

    hl = CCXTExchange("hyperliquid", api_key="0xabc", secret="0xkey")
    hl.client = FakeClient()
    await hl.place_stop_order(BTC_PERP, Side.BUY, 1, 105.0)
    create = next(c for c in hl.client.calls if c[0] == "create_order")
    assert create[1] == "BTC/USDC:USDC" and create[5] == 105.0  # 市價觸發單需要參考價

    okx = CCXTExchange("okx", api_key="k", secret="s", passphrase="p")
    okx.client = FakeClient()
    okx.client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    okx.client.price_to_precision = lambda sym, p: f"{p:.1f}"
    okx._markets_loaded = True
    await okx.place_stop_order(BTC_PERP, Side.SELL, 0.25, 95.04)
    create = next(c for c in okx.client.calls if c[0] == "create_order")
    assert create[4] == pytest.approx(25) and create[6]["stopLossPrice"] == 95.0
