"""進場分析：盈虧比、等得到的機率、期望值、建議；引擎掛單等待"""

import pytest

from goldhunter.analysis.entry import _simulate, analyze_entry, strategy_signal_indices
from goldhunter.core.models import Candle
from goldhunter.strategies import ta
from goldhunter.strategies.builtin import MACrossStrategy

from .conftest import BTC_PERP, make_candles


def test_rr_and_candidates_long(candles):
    a = analyze_entry(candles[:-1], "1h", "long")
    m = a.candidates[0]
    assert m.key == "market" and m.fill_prob == 1.0
    assert m.rr == pytest.approx(abs(a.target - a.price) / abs(a.price - a.stop), abs=0.01)
    for c in a.candidates[1:]:
        assert c.price < a.price and c.price > a.stop  # 多單的等待點位在現價下方、止損上方
        assert c.rr > m.rr  # 進場越低盈虧比越好
        assert 0 <= c.fill_prob <= 1
    assert a.recommended in {c.key for c in a.candidates} | {"skip"}
    assert a.recommendation


def test_short_mirror_and_given_stop_target(candles):
    price = candles[-2].close
    a = analyze_entry(candles[:-1], "1h", "short", stop=price * 1.02, target=price * 0.95)
    assert a.stop_source == "策略 / AI" and a.target_source == "策略 / AI"
    assert a.candidates[0].rr == pytest.approx(2.5, abs=0.01)
    for c in a.candidates[1:]:
        assert a.price < c.price < a.stop


def test_invalid_stop_falls_back_to_atr(candles):
    price = candles[-2].close
    a = analyze_entry(candles[:-1], "1h", "long", stop=price * 1.1)  # 多單止損在現價上方＝無效
    assert a.stop_source == "2 倍 ATR" and a.stop < price


def test_high_frequency_recommends_market():
    c = make_candles(400, step_ms=60_000)
    a = analyze_entry(c[:-1], "1m", "long")
    assert a.high_frequency and a.recommended in ("market", "skip")


def test_simulate_fill_and_win():
    # 每根 K 棒都下探 1 ATR、上攻 1 ATR：回檔 0.5 ATR 一定等得到
    cs, p = [], 100.0
    for i in range(200):
        cs.append(Candle(ts=i, open=p, high=p + 1, low=p - 1, close=p, volume=1))
    atr = ta.atr([c.high for c in cs], [c.low for c in cs], [c.close for c in cs], 14)
    fill, win, n = _simulate(cs, atr, list(range(20, 150)), True, entry_off=0.25, stop_off=5, target_off=0.5, wait=5)
    assert fill == 1.0 and win == 1.0 and n > 50


async def test_signal_indices_from_strategy(candles):
    idx = await strategy_signal_indices(MACrossStrategy({"fast": 5, "slow": 20}), BTC_PERP, "1h", candles, "long")
    assert len(idx) >= 3
    f, sl = ta.ema([c.close for c in candles], 5), ta.ema([c.close for c in candles], 20)
    for i in idx:  # 訊號 K 棒確實是快線上穿慢線
        assert f[i] > sl[i] and f[i - 1] <= sl[i - 1]
    a = analyze_entry(candles[:-1], "1h", "long", signal_idx=idx)
    assert "訊號" in a.sample_basis or "K 棒" in a.sample_basis


def test_trend_direction():
    from goldhunter.analysis.entry import trend_direction

    up = make_candles(200, amp=0.1)  # 緩步上漲
    assert trend_direction(up)[0] == "long"
    down = [c.model_copy(update={"close": 1000 - c.close}) for c in up]
    assert trend_direction(down)[0] == "short" and "EMA50" in trend_direction(down)[1]
