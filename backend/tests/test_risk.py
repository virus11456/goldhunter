import pytest

from goldhunter.core.models import Action, Decision, Position, Side
from goldhunter.exchanges.base import Capabilities
from goldhunter.risk.manager import RiskConfig, RiskState, evaluate

from .conftest import AAPL, BTC_PERP

PERP_CAPS = Capabilities(supports_short=True, max_leverage=50)
CASH_CAPS = Capabilities(supports_short=False, max_leverage=1)


def run(decision, *, config=None, state=None, equity=10_000, price=100, position=None, caps=PERP_CAPS,
        positions=None):
    return evaluate(decision, config=config or RiskConfig(), state=state or RiskState(), equity=equity,
                    price=price, position=position, all_positions=positions or ([position] if position else []),
                    capabilities=caps, round_qty=lambda i, q: round(q, 6))


def test_hold_is_not_executed():
    assert not run(Decision(instrument=BTC_PERP, action=Action.HOLD)).approved


def test_close_always_allowed():
    pos = Position(instrument=BTC_PERP, quantity=2, entry_price=90)
    r = run(Decision(instrument=BTC_PERP, action=Action.CLOSE), position=pos)
    assert r.approved and r.orders[0].side == Side.SELL and r.orders[0].reduce_only
    assert r.orders[0].quantity == 2


def test_clamps_size_and_leverage_and_adds_stop():
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=80, leverage=20)
    r = run(d, config=RiskConfig(max_position_pct=10, max_leverage=3, max_total_exposure_pct=500))
    assert r.approved
    assert r.adjusted.leverage == 3 and r.adjusted.size_pct == 10
    assert r.adjusted.stop_loss == pytest.approx(97)
    # 10% * 3x * 10000 / 100 = 30
    assert r.orders[-1].quantity == pytest.approx(30)


def test_reject_short_when_not_supported():
    d = Decision(instrument=AAPL, action=Action.OPEN_SHORT, size_pct=10, stop_loss=110)
    r = run(d, caps=CASH_CAPS)
    assert not r.approved and any("放空" in x for x in r.reasons)


def test_reject_wrong_side_stop():
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10, stop_loss=105)
    assert not run(d).approved


def test_require_stop_without_default_rejects():
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10)
    r = run(d, config=RiskConfig(default_stop_loss_pct=None))
    assert not r.approved and "未設定止損" in r.reasons


def test_daily_loss_circuit_breaker():
    st = RiskState()
    st.roll_day(10_000)
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10, stop_loss=95)
    r = run(d, state=st, equity=9_400, config=RiskConfig(daily_loss_limit_pct=5))
    assert not r.approved and st.halted_reason


def test_no_pyramiding_by_default():
    pos = Position(instrument=BTC_PERP, quantity=1, entry_price=100)
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10, stop_loss=95)
    assert not run(d, position=pos).approved


def test_reverse_closes_first():
    pos = Position(instrument=BTC_PERP, quantity=-1, entry_price=100)
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10, stop_loss=95)
    r = run(d, position=pos)
    assert r.approved and len(r.orders) == 2 and r.orders[0].reduce_only


def test_order_rate_limit():
    st = RiskState()
    st.record_orders(5)
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10, stop_loss=95)
    assert not run(d, state=st, config=RiskConfig(max_orders_per_hour=5)).approved


def test_min_confidence():
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10, stop_loss=95, confidence=0.3)
    assert not run(d, config=RiskConfig(min_confidence=0.5)).approved


def test_max_positions_and_long_only():
    eth = BTC_PERP.model_copy(update={"symbol": "ETH/USDT"})
    held = [Position(instrument=eth, quantity=1, entry_price=100)]
    d = Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10)
    r = run(d, config=RiskConfig(max_positions=1), positions=held)
    assert not r.approved and any("組合上限" in x for x in r.reasons)
    assert run(d, config=RiskConfig(max_positions=2), positions=held).approved
    s = Decision(instrument=BTC_PERP, action=Action.OPEN_SHORT, size_pct=10)
    r = run(s, config=RiskConfig(long_only=True))
    assert not r.approved and any("只做多" in x for x in r.reasons)
