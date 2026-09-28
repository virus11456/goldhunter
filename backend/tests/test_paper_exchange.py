import pytest

from goldhunter.core.models import OrderRequest, OrderStatus, Side
from goldhunter.exchanges.paper import PaperExchange

from .conftest import AAPL, BTC_PERP


def ex(cash=10_000):
    e = PaperExchange(initial_cash=cash, fee_rate=0.0, slippage=0.0)
    e.set_price(AAPL, 100)
    e.set_price(BTC_PERP, 100)
    return e


async def test_cash_buy_sell_pnl():
    e = ex()
    o = await e.place_order(OrderRequest(instrument=AAPL, side=Side.BUY, quantity=10))
    assert o.status == OrderStatus.FILLED
    assert e.cash == pytest.approx(9_000)
    e.set_price(AAPL, 110)
    assert e.equity() == pytest.approx(10_100)
    await e.place_order(OrderRequest(instrument=AAPL, side=Side.SELL, quantity=10, reduce_only=True))
    assert e.cash == pytest.approx(10_100)
    assert not e.positions
    assert e.realized_pnl == pytest.approx(100)


async def test_cash_instrument_cannot_short():
    e = ex()
    o = await e.place_order(OrderRequest(instrument=AAPL, side=Side.SELL, quantity=1))
    assert o.status == OrderStatus.REJECTED
    assert "放空" in o.raw["reason"]


async def test_perp_short_with_leverage():
    e = ex()
    await e.place_order(OrderRequest(instrument=BTC_PERP, side=Side.SELL, quantity=20, leverage=5))
    assert e.cash == pytest.approx(10_000 - 20 * 100 / 5)
    assert e.equity() == pytest.approx(10_000)
    e.set_price(BTC_PERP, 90)
    assert e.equity() == pytest.approx(10_200)
    await e.place_order(OrderRequest(instrument=BTC_PERP, side=Side.BUY, quantity=20, reduce_only=True))
    assert e.cash == pytest.approx(10_200)


async def test_insufficient_funds_rejected():
    e = ex(cash=100)
    o = await e.place_order(OrderRequest(instrument=AAPL, side=Side.BUY, quantity=5))
    assert o.status == OrderStatus.REJECTED


async def test_fees_and_slippage():
    e = PaperExchange(initial_cash=10_000, fee_rate=0.001, slippage=0.01)
    e.set_price(AAPL, 100)
    o = await e.place_order(OrderRequest(instrument=AAPL, side=Side.BUY, quantity=1))
    assert o.avg_price == pytest.approx(101)
    assert o.fee == pytest.approx(0.101)
