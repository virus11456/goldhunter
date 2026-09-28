import json

import httpx2 as httpx
import pytest

from goldhunter.ai.anthropic_provider import AnthropicProvider
from goldhunter.ai.base import DECISION_SCHEMA
from goldhunter.backtest.engine import BacktestConfig, run_backtest
from goldhunter.core.models import Action, Balance, Instrument
from goldhunter.exchanges.base import Capabilities
from goldhunter.strategies.ai_strategy import AIStrategy, build_market_prompt
from goldhunter.strategies.base import StrategyContext
from goldhunter.strategies.builtin import MACrossStrategy, RSIReversionStrategy
from goldhunter.tradingview.webhook import TradingViewAlert, alert_to_decision, check_passphrase

from .conftest import AAPL, BTC_PERP
from .test_custom_strategy import FakeAI


async def test_backtest_ma_cross_perp(candles):
    r = await run_backtest(MACrossStrategy({"fast": 5, "slow": 20}), BTC_PERP, "1h", candles, BacktestConfig())
    m = r.metrics
    assert m["trades"] >= 4 and m["closed_trades"] >= 2
    assert {"total_return_pct", "max_drawdown_pct", "sharpe", "win_rate_pct"} <= m.keys()
    assert len(r.equity_curve) == len(candles)
    # 無未來函數：每筆非止損成交都發生在某根 K 棒的開盤價（含滑價）
    opens = {c.ts: c.open for c in candles}
    for t in r.trades:
        if t.reason not in ("止損", "止盈"):
            assert t.price == pytest.approx(opens[t.ts], rel=0.001)


async def test_backtest_never_shorts_when_not_supported(candles):
    r = await run_backtest(MACrossStrategy({"fast": 5, "slow": 20}), AAPL, "1h", candles)
    pos = 0.0
    for t in r.trades:
        pos += t.quantity if t.side == "buy" else -t.quantity
        assert pos >= -1e-9


async def test_backtest_rsi(candles):
    r = await run_backtest(RSIReversionStrategy(), BTC_PERP, "1h", candles)
    assert r.metrics["bars"] == len(candles)


def _ctx(candles, pos=None):
    return StrategyContext(instrument=BTC_PERP, timeframe="1h", candles=candles, position=pos,
                           balance=Balance(currency="USDT", total=10_000, free=10_000),
                           capabilities=Capabilities(supports_short=True, max_leverage=50), max_leverage=3)


async def test_ai_strategy_parses_and_applies_confidence(candles):
    ai = FakeAI([
        {"action": "open_long", "size_pct": 10, "leverage": 2, "stop_loss": 90, "take_profit": 130,
         "confidence": 0.9, "reasoning": "趨勢向上"},
        {"action": "open_long", "size_pct": 10, "leverage": 2, "stop_loss": 90, "take_profit": None,
         "confidence": 0.2, "reasoning": "不確定"},
    ])
    s = AIStrategy({"min_confidence": 0.5}, ai=ai)
    d1 = await s.run(_ctx(candles))
    assert d1.action == Action.OPEN_LONG and d1.source == "ai" and d1.leverage == 2
    d2 = await s.run(_ctx(candles))
    assert d2.action == Action.HOLD


def test_crypto_spot_rejected():
    with pytest.raises(ValueError, match="永續"):
        Instrument.parse("crypto:BTC/USDT:spot")
    assert BTC_PERP.ccxt_symbol == "BTC/USDT:USDT"


def test_market_prompt_contains_key_info(candles):
    p = build_market_prompt(_ctx(candles), "只做多", bars=10)
    assert "crypto:BTC/USDT:perp" in p and "rsi14" in p and "只做多" in p


def test_tradingview_alert_mapping():
    a = TradingViewAlert(passphrase="x", action="buy", market_position="long", size_pct=5)
    d = alert_to_decision(a, BTC_PERP, 0, default_size_pct=10)
    assert d.action == Action.OPEN_LONG and d.size_pct == 5 and d.source == "tradingview"
    assert alert_to_decision(TradingViewAlert(action="sell", market_position="flat"), BTC_PERP, 1, 10).action == Action.CLOSE
    assert alert_to_decision(TradingViewAlert(action="sell"), BTC_PERP, 1, 10).action == Action.CLOSE
    assert alert_to_decision(TradingViewAlert(action="sell"), BTC_PERP, 0, 10).action == Action.OPEN_SHORT
    with pytest.raises(ValueError):
        alert_to_decision(TradingViewAlert(action="???"), BTC_PERP, 0, 10)
    assert check_passphrase(TradingViewAlert(passphrase="abc"), "abc")
    assert not check_passphrase(TradingViewAlert(passphrase=""), "")


def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


async def test_anthropic_provider_request_shape():
    """用假的 HTTP 傳輸層驗證送給 Claude API 的請求內容（不會真的連網）"""
    captured = {}
    answer = json.dumps({"action": "hold", "size_pct": 0, "leverage": 1, "stop_loss": None, "take_profit": None,
                         "confidence": 0.5, "reasoning": "觀望"})

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5", "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}
        body = _sse([
            {"type": "message_start", "message": msg},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": answer}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
             "usage": {"output_tokens": 20}},
            {"type": "message_stop"},
        ])
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    p = AnthropicProvider(api_key="sk-test", effort="medium")
    p.client = p.client.with_options(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = await p.complete_json("sys", "user", DECISION_SCHEMA)
    assert r.decision["action"] == "hold" and r.output_tokens == 20
    body = captured["body"]
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "medium"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in captured["headers"]["anthropic-beta"]
    assert body["stream"] is True
