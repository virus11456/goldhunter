"""MRSPENCER v4.6B：用人工 1 分 K 黃金走勢驗證進場訊號、止盈、加碼 / 攤平 / 配對、整籃停損與冷卻、交易時段"""

from datetime import UTC, datetime

import pytest

from goldhunter.backtest.engine import BacktestConfig, run_backtest
from goldhunter.core.models import Candle, Instrument
from goldhunter.risk.manager import RiskConfig
from goldhunter.strategies.mrspencer import MrSpencerStrategy, next_rung, suffix_sum

GOLD = Instrument.parse("crypto:PAXG/USDT:perp")
# 2026-09-01 07:00 UTC ＝ 台北 15:00；第 60 根起進入 16:00–23:00 交易時段
T0 = int(datetime(2026, 9, 1, 7, 0, tzinfo=UTC).timestamp() * 1000) - 340 * 60_000


def build(path: list[float]) -> list[Candle]:
    out, prev = [], path[0]
    for i, c in enumerate(path):
        out.append(Candle(ts=T0 + i * 60_000, open=prev, high=max(prev, c) + 0.05, low=min(prev, c) - 0.05,
                          close=c, volume=1))
        prev = c
    return out


def base_path() -> list[float]:
    """400 根 3700±3 盤整 → 10 根急跌到 3675 → 在低點緩跌滯留 25 根（撞到區間底、動能 ≤ -15）"""
    p = [3700 + 3 * ((i % 20) / 10 - 1) for i in range(400)]
    p += [3700 - 2.5 * (i + 1) for i in range(10)]
    p += [3675 - 0.02 * i for i in range(25)]
    return p


def cfg():
    return BacktestConfig(initial_cash=1500, fee_rate=0, slippage=0,
                          risk=RiskConfig(**{**MrSpencerStrategy.recommended_risk, "max_orders_per_hour": 10_000}))


def test_ladder_sizes():
    assert suffix_sum(8) == pytest.approx(1.15) and next_rung(8) == pytest.approx(0.35)
    assert next_rung(13) == pytest.approx(1.3)


async def test_entry_then_take_profit():
    path = base_path()
    path += [path[-1] + 0.2 * i for i in range(1, 30)]  # 反彈 > 2.3
    path += [3680.0] * 5
    r = await run_backtest(MrSpencerStrategy(), GOLD, "1m", build(path), cfg())
    opens = [t for t in r.trades if not t.reduce_only]
    closes = [t for t in r.trades if t.reduce_only]
    assert len(opens) == 1 and opens[0].side == "buy"
    assert opens[0].quantity == pytest.approx(1.15)  # 初始資金 1,500 → 1 倍口數
    assert len(closes) == 1 and closes[0].realized_pnl > 2.3 * 1.15 * 0.9
    assert "TP+2.3" in closes[0].reason


async def test_rescue_ladder_stop_and_cooldown():
    path = base_path()
    last = path[-1]
    path += [last - 0.5 * i for i in range(1, 121)]  # 兩小時內下跌 60 美元 → 加碼、攤平、配對，最後整籃停損
    bottom = path[-1]
    path += [bottom + 3 * ((i % 20) / 10 - 1) for i in range(150)]  # 冷卻期內再盤整
    r = await run_backtest(MrSpencerStrategy(), GOLD, "1m", build(path), cfg())
    opens = [t for t in r.trades if not t.reduce_only]
    closes = [t for t in r.trades if t.reduce_only]
    assert opens[0].quantity == pytest.approx(1.15)
    reasons = " ".join(t.reason for t in opens)
    assert "爬梯加碼" in reasons and "攤平1" in reasons and "攤平2" in reasons and "配對救援" in reasons
    assert sum(t.quantity for t in opens) <= 9.0 * 1.2  # 單次檢查上限 9 口（與 Pine 相同，不累計）
    assert len(closes) == 1 and "籃停損" in closes[0].reason and closes[0].realized_pnl < 0
    stop_ts = closes[0].ts
    assert not [t for t in opens if t.ts > stop_ts]  # 冷卻 240 根內不再進場


async def test_rescue_geometric_sizes():
    s = MrSpencerStrategy({"addonOn": False, "relayOn": False, "stopUsd": 0, "trapCutMin": 0})
    path = base_path()
    path += [path[-1] - 0.25 * i for i in range(1, 241)]  # 一路下跌（均價跟著下移）：攤平 1、2、3 後不再攤平
    path += [path[-1]] * 3
    r = await run_backtest(s, GOLD, "1m", build(path), cfg())
    adds = [t.quantity for t in r.trades if not t.reduce_only][1:]
    assert adds == pytest.approx([1.15, 1.15 * 1.3, 1.15 * 1.3 ** 2], rel=1e-3)


async def test_no_entry_outside_session_and_equity_scaling():
    path = base_path()
    path += [path[-1] + 0.2 * i for i in range(1, 30)]
    c = build(path)
    shifted = [x.model_copy(update={"ts": x.ts + 8 * 3_600_000}) for x in c]  # 挪到台北 23:00 之後
    r = await run_backtest(MrSpencerStrategy(), GOLD, "1m", shifted, cfg())
    assert not r.trades
    r2 = await run_backtest(MrSpencerStrategy(), GOLD, "1m", c,
                            cfg().model_copy(update={"initial_cash": 3000}))
    assert r2.trades[0].quantity == pytest.approx(2.3)  # 權益 3,000 → 口數 ×2
    r3 = await run_backtest(MrSpencerStrategy({"scale_with_equity": False, "unit": 0.1}), GOLD, "1m", c,
                            cfg().model_copy(update={"initial_cash": 3000}))
    assert r3.trades[0].quantity == pytest.approx(0.115)


def test_api_create_paper_first_and_recommended_risk():
    from fastapi.testclient import TestClient

    from goldhunter.main import app

    h = {"Authorization": "Bearer test-token"}
    with TestClient(app) as c:
        types = {t["type"]: t for t in c.get("/api/meta", headers=h).json()["strategy_types"]}
        assert types["mrspencer"]["recommended_risk"]["allow_pyramiding"] is True
        st = c.post("/api/strategies", headers=h, json={"name": "MRSPENCER v4.6B", "kind": "mrspencer",
                                                        "params": {"unit": 0.5}}).json()
        assert st["status"] == "paper_only"
        acc = c.post("/api/accounts", headers=h, json={"name": "p", "exchange_id": "okx"}).json()
        body = {"name": "黃金", "account_id": acc["id"], "strategy_id": st["id"],
                "symbols": ["crypto:PAXG/USDT:perp"], "timeframe": "1m", "interval_sec": 20,
                "risk": {"max_leverage": 10}}
        b = c.post("/api/bots", headers=h, json=body).json()
        assert b["risk"]["allow_pyramiding"] is True and b["risk"]["max_total_exposure_pct"] == 2500
        assert b["risk"]["max_leverage"] == 10  # 使用者設定優先
