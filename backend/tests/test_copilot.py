from datetime import UTC, datetime, timedelta

import pytest

from goldhunter.copilot.config import CopilotConfig
from goldhunter.copilot.review import constrain_manage, constrain_review
from goldhunter.copilot.tune import better, clamp_params, tune_strategy
from goldhunter.core.models import Action, Decision, Position
from goldhunter.intel.hub import IntelSnapshot
from goldhunter.risk.manager import RiskConfig
from goldhunter.strategies.builtin import MACrossStrategy

from .conftest import BTC_PERP
from .test_custom_strategy import FakeAI

CFG = CopilotConfig(size_mult_min=0.5, size_mult_max=1.5)


def long_signal(**kw):
    return Decision(instrument=BTC_PERP, action=Action.OPEN_LONG, size_pct=10, stop_loss=95, take_profit=None,
                    reasoning="均線交叉", **kw)


def test_review_veto():
    out = constrain_review({"verdict": "veto", "size_multiplier": 1, "stop_loss": None, "take_profit": None,
                            "confidence": 0.8, "reasoning": "CPI 即將公布"}, long_signal(), 100, CFG)
    assert out.verdict == "veto" and out.decision is None


def test_review_clamps_size_and_rejects_looser_stop():
    raw = {"verdict": "adjust", "size_multiplier": 5, "stop_loss": 90, "take_profit": 120, "confidence": 0.7,
           "reasoning": "趨勢強"}
    out = constrain_review(raw, long_signal(), 100, CFG)
    d = out.decision
    assert d.action == Action.OPEN_LONG  # 方向不變
    assert d.size_pct == pytest.approx(15)  # 5x 被限制為 1.5x
    assert d.stop_loss == 95  # 90 比原本寬，拒絕
    assert d.take_profit == 120
    assert d.source == "strategy+ai"
    assert any("1.5" in n for n in out.notes) and any("止損" in n for n in out.notes)


def test_review_accepts_tighter_stop():
    raw = {"verdict": "adjust", "size_multiplier": 0.5, "stop_loss": 97, "take_profit": None, "confidence": 0.6,
           "reasoning": "波動大，縮倉"}
    d = constrain_review(raw, long_signal(), 100, CFG).decision
    assert d.stop_loss == 97 and d.size_pct == pytest.approx(5)


def test_review_min_confidence_vetoes():
    raw = {"verdict": "approve", "size_multiplier": 1, "stop_loss": None, "take_profit": None, "confidence": 0.3,
           "reasoning": "不確定"}
    out = constrain_review(raw, long_signal(), 100, CopilotConfig(review_min_confidence=0.5))
    assert out.verdict == "veto"


def test_review_garbage_is_veto():
    out = constrain_review({"verdict": "go_short_now", "size_multiplier": 1, "stop_loss": None,
                            "take_profit": None, "confidence": 1, "reasoning": ""}, long_signal(), 100, CFG)
    assert out.verdict == "veto"


def test_manage_only_risk_reducing():
    long_pos = Position(instrument=BTC_PERP, quantity=1, entry_price=100)
    # 多單止損只能上移，且不能超過現價
    assert constrain_manage({"action": "move_stop", "new_stop": 101, "reduce_pct": None, "reasoning": ""},
                            long_pos, 95, 105).new_stop == 101
    assert constrain_manage({"action": "move_stop", "new_stop": 90, "reduce_pct": None, "reasoning": ""},
                            long_pos, 95, 105).action == "hold"
    assert constrain_manage({"action": "move_stop", "new_stop": 106, "reduce_pct": None, "reasoning": ""},
                            long_pos, 95, 105).action == "hold"
    assert constrain_manage({"action": "reduce", "reduce_pct": 150, "new_stop": None, "reasoning": ""},
                            long_pos, 95, 105).action == "hold"
    assert constrain_manage({"action": "add_more", "reduce_pct": None, "new_stop": None, "reasoning": ""},
                            long_pos, 95, 105).action == "hold"
    short_pos = Position(instrument=BTC_PERP, quantity=-1, entry_price=100)
    assert constrain_manage({"action": "move_stop", "new_stop": 99, "reduce_pct": None, "reasoning": ""},
                            short_pos, 104, 95).new_stop == 99


def test_clamp_params_and_types():
    cur = {"fast": 20, "slow": 50, "atr_mult": 2.0, "allow_short": True}
    out = clamp_params({"fast": 3.7, "slow": 999, "atr_mult": 1.25, "allow_short": False},
                       cur, {"fast": [5, 30], "slow": [30, 100], "atr_mult": [1, 3]})
    assert out == {"fast": 5, "slow": 100, "atr_mult": 1.25, "allow_short": True}
    assert isinstance(out["fast"], int)


def test_better():
    assert better({"total_return_pct": 1, "max_drawdown_pct": 5}, {"total_return_pct": 2, "max_drawdown_pct": 5.5})
    assert not better({"total_return_pct": 1, "max_drawdown_pct": 5}, {"total_return_pct": 2, "max_drawdown_pct": 9})
    assert not better({"total_return_pct": 3, "max_drawdown_pct": 5}, {"total_return_pct": 2, "max_drawdown_pct": 1})


async def test_tune_strategy(candles):
    ai = FakeAI([{"params": {"fast": 8, "slow": 25}, "reasoning": "盤勢震盪，縮短週期"}])
    cfg = CopilotConfig(tune=True, tune_ranges={"fast": [5, 15], "slow": [20, 40]})
    out = await tune_strategy(ai, MACrossStrategy, {**MACrossStrategy.default_params, "fast": 10, "slow": 30},
                              BTC_PERP, "1h", candles, cfg, RiskConfig())
    assert out.proposed_params["fast"] == 8 and out.proposed_params["slow"] == 25
    assert out.current_metrics and out.proposed_metrics
    assert out.passed == better(out.current_metrics, out.proposed_metrics)


async def test_tune_requires_ranges(candles):
    with pytest.raises(ValueError):
        await tune_strategy(FakeAI([]), MACrossStrategy, MACrossStrategy.default_params, BTC_PERP, "1h", candles,
                            CopilotConfig(), RiskConfig())


def test_event_window_and_prompt():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    snap = IntelSnapshot(instrument=str(BTC_PERP), fetched_at="x",
                         events=[{"title": "CPI m/m", "time": (now + timedelta(minutes=30)).isoformat(),
                                  "country": "USD"}],
                         fear_greed={"value": 20, "label": "Extreme Fear", "yesterday": 25},
                         news=[{"title": "Bitcoin ETF inflows", "published": "2026-09-28T11:00:00"}])
    assert snap.upcoming_event_within(60, now)["title"] == "CPI m/m"
    assert snap.upcoming_event_within(10, now) is None
    p = snap.to_prompt()
    assert "Extreme Fear" in p and "CPI m/m" in p and "Bitcoin ETF inflows" in p
