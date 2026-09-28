import pytest

from goldhunter.strategies import ta


def test_sma():
    assert ta.sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_ema_seeds_with_sma():
    out = ta.ema([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == 2
    assert out[3] == pytest.approx(3.0)  # 0.5*4 + 0.5*2


def test_rsi_bounds_and_monotonic_up():
    up = list(range(1, 40))
    r = ta.rsi(up, 14)
    assert r[-1] == 100.0
    mixed = [10, 11, 10.5, 11.5, 11, 12, 11.2, 12.4, 12, 13, 12.5, 13.3, 13, 14, 13.4, 14.5]
    v = ta.rsi(mixed, 14)[-1]
    assert 0 < v < 100


def test_atr_positive():
    hi, lo, c = [10, 11, 12, 13], [9, 10, 11, 12], [9.5, 10.5, 11.5, 12.5]
    assert ta.atr(hi, lo, c, 2)[-1] > 0


def test_crossover_and_crossunder():
    assert ta.crossover([1, 3], [2, 2])
    assert not ta.crossover([3, 4], [2, 2])
    assert ta.crossunder([3, 1], [2, 2])
    assert ta.crossover([1, 3], 2)  # 與常數比較
    assert not ta.crossover([None, 3], [2, 2])


def test_bb_and_macd_shapes():
    src = [float(i % 7) for i in range(60)]
    basis, upper, lower = ta.bb(src, 20, 2)
    assert len(basis) == 60 and upper[-1] >= basis[-1] >= lower[-1]
    line, sig, hist = ta.macd(src)
    assert len(hist) == 60 and hist[-1] == pytest.approx(line[-1] - sig[-1])


def test_highest_lowest():
    assert ta.highest([1, 5, 3, 2], 2) == [None, 5, 5, 3]
    assert ta.lowest([1, 5, 3, 2], 2) == [None, 1, 3, 2]
