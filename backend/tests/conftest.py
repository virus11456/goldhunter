from __future__ import annotations

import math
import os
import tempfile

import pytest

# 測試用獨立資料目錄（必須在 import goldhunter 之前設定）
_tmp = tempfile.mkdtemp(prefix="goldhunter-test-")
os.environ["GOLDHUNTER_DATA_DIR"] = _tmp
os.environ["GOLDHUNTER_API_TOKEN"] = "test-token"
os.environ["GOLDHUNTER_RESUME_BOTS"] = "false"

from goldhunter.core.models import Candle, Instrument  # noqa: E402

BTC_PERP = Instrument.parse("crypto:BTC/USDT:perp")
# 非槓桿、不可放空的現金型商品（未來的股票）用來測試相關邏輯
AAPL = Instrument.parse("us:AAPL:stock")


def make_candles(n: int = 400, start: float = 100.0, period: int = 80, amp: float = 20.0,
                 step_ms: int = 3_600_000) -> list[Candle]:
    """正弦波行情，均線交叉策略一定會產生多次訊號"""
    out = []
    prev = start
    for i in range(n):
        c = start + amp * math.sin(2 * math.pi * i / period) + i * 0.02
        o = prev
        out.append(Candle(ts=1_700_000_000_000 + i * step_ms, open=o, high=max(o, c) + 0.5,
                          low=min(o, c) - 0.5, close=c, volume=10))
        prev = c
    return out


@pytest.fixture
def candles():
    return make_candles()
