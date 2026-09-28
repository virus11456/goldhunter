"""技術指標（純 Python，命名刻意貼近 TradingView Pine Script 的 ta.* 以便轉換）。

所有函式輸入為 list[float]，回傳與輸入等長的 list，資料不足的位置為 None。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

Series = Sequence[float | None]


def _nz(v: float | None, default: float = 0.0) -> float:
    return default if v is None or (isinstance(v, float) and math.isnan(v)) else v


def sma(src: Series, length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(src)
    window_sum, count = 0.0, 0
    for i, v in enumerate(src):
        if v is None:
            window_sum, count = 0.0, 0
            continue
        window_sum += v
        count += 1
        if count > length:
            window_sum -= src[i - length]  # type: ignore[operator]
            count = length
        if count == length:
            out[i] = window_sum / length
    return out


def ema(src: Series, length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(src)
    alpha = 2 / (length + 1)
    prev: float | None = None
    seed: list[float] = []
    for i, v in enumerate(src):
        if v is None:
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == length:
                prev = sum(seed) / length
                out[i] = prev
            continue
        prev = alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def rma(src: Series, length: int) -> list[float | None]:
    """Wilder 平滑（Pine 的 ta.rma），RSI / ATR 使用"""
    out: list[float | None] = [None] * len(src)
    alpha = 1 / length
    prev: float | None = None
    seed: list[float] = []
    for i, v in enumerate(src):
        if v is None:
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == length:
                prev = sum(seed) / length
                out[i] = prev
            continue
        prev = alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def change(src: Series, length: int = 1) -> list[float | None]:
    return [
        None if i < length or src[i] is None or src[i - length] is None else src[i] - src[i - length]  # type: ignore[operator]
        for i in range(len(src))
    ]


def rsi(src: Series, length: int = 14) -> list[float | None]:
    ch = change(src)
    gains = [None if c is None else max(c, 0.0) for c in ch]
    losses = [None if c is None else max(-c, 0.0) for c in ch]
    up, down = rma(gains, length), rma(losses, length)
    out: list[float | None] = []
    for u, d in zip(up, down):
        if u is None or d is None:
            out.append(None)
        elif d == 0:
            out.append(100.0)
        else:
            out.append(100 - 100 / (1 + u / d))
    return out


def tr(high: Series, low: Series, close: Series) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(close)):
        h, lo = _nz(high[i]), _nz(low[i])
        if i == 0 or close[i - 1] is None:
            out.append(h - lo)
        else:
            pc = close[i - 1]
            out.append(max(h - lo, abs(h - pc), abs(lo - pc)))  # type: ignore[arg-type]
    return out


def atr(high: Series, low: Series, close: Series, length: int = 14) -> list[float | None]:
    return rma(tr(high, low, close), length)


def stdev(src: Series, length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(src)
    for i in range(length - 1, len(src)):
        w = src[i - length + 1 : i + 1]
        if any(x is None for x in w):
            continue
        m = sum(w) / length  # type: ignore[arg-type]
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / length)  # type: ignore[operator]
    return out


def bb(src: Series, length: int = 20, mult: float = 2.0):
    """布林通道，回傳 (中軌, 上軌, 下軌)"""
    basis, dev = sma(src, length), stdev(src, length)
    upper = [None if b is None or d is None else b + mult * d for b, d in zip(basis, dev)]
    lower = [None if b is None or d is None else b - mult * d for b, d in zip(basis, dev)]
    return basis, upper, lower


def macd(src: Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """回傳 (macd 線, 訊號線, 柱狀體)"""
    f, s = ema(src, fast), ema(src, slow)
    line = [None if a is None or b is None else a - b for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    return line, sig, hist


def highest(src: Series, length: int) -> list[float | None]:
    return [None if i < length - 1 else max(_nz(x, -math.inf) for x in src[i - length + 1 : i + 1])
            for i in range(len(src))]


def lowest(src: Series, length: int) -> list[float | None]:
    return [None if i < length - 1 else min(_nz(x, math.inf) for x in src[i - length + 1 : i + 1])
            for i in range(len(src))]


def _pair(a: Series | float, b: Series | float, n: int):
    sa = a if isinstance(a, Sequence) else [a] * n
    sb = b if isinstance(b, Sequence) else [b] * n
    return sa, sb


def crossover(a: Series | float, b: Series | float) -> bool:
    """最後一根 K 棒 a 由下往上穿越 b（Pine 的 ta.crossover）"""
    n = len(a) if isinstance(a, Sequence) else len(b)  # type: ignore[arg-type]
    sa, sb = _pair(a, b, n)
    if n < 2 or None in (sa[-1], sa[-2], sb[-1], sb[-2]):
        return False
    return sa[-2] <= sb[-2] and sa[-1] > sb[-1]  # type: ignore[operator]


def crossunder(a: Series | float, b: Series | float) -> bool:
    n = len(a) if isinstance(a, Sequence) else len(b)  # type: ignore[arg-type]
    sa, sb = _pair(a, b, n)
    if n < 2 or None in (sa[-1], sa[-2], sb[-1], sb[-2]):
        return False
    return sa[-2] >= sb[-2] and sa[-1] < sb[-1]  # type: ignore[operator]
