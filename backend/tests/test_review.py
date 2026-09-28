"""Pine 轉換自動審查（四關）與策略上線流程"""

from datetime import UTC, datetime, timedelta

import pytest

from goldhunter.backtest.engine import BacktestConfig, run_backtest
from goldhunter.risk.manager import RiskConfig
from goldhunter.strategies.custom import load_strategy_class
from goldhunter.tradingview.review import compare_entries, parse_tv_trades_csv, run_review

from .conftest import BTC_PERP, make_candles
from .test_custom_strategy import GOOD, PINE, FakeAI, extract_code

CODE = extract_code(GOOD)
EQUIV = {"equivalent": True, "confidence": 0.92, "issues": [{"severity": "low", "description": "未轉換 plot"}],
         "summary": "邏輯一致"}
NOT_EQUIV = {"equivalent": False, "confidence": 0.85,
             "issues": [{"severity": "high", "description": "出場條件寫成 crossover，應為 crossunder"}],
             "summary": "出場條件相反"}


def candles_fetcher(candles):
    async def fetch(start, end):
        return candles
    return fetch


async def tv_csv_from_backtest(candles, tz_hours=8, drop=0, lang="en"):
    """用我們自己的回測結果產生 TradingView 格式的交易清單 CSV（時間轉成 UTC+tz_hours）"""
    cls = load_strategy_class(CODE)
    res = await run_backtest(cls(cls.default_params), BTC_PERP, "1h", candles,
                             BacktestConfig(risk=RiskConfig(daily_loss_limit_pct=0, max_orders_per_hour=10_000,
                                                            max_position_pct=100, max_total_exposure_pct=1000,
                                                            require_stop_loss=False)))
    if lang == "en":
        rows = ["Trade #,Type,Signal,Date/Time,Price USDT,Contracts,Profit USDT"]
        entry, exit_ = "Entry Long", "Exit Long"
    else:
        rows = ["交易 #,類型,訊號,日期/時間,價格 USDT,合約,獲利 USDT"]
        entry, exit_ = "多頭進場", "多頭出場"
    n = 0
    entries = [t for t in res.trades if not t.reduce_only][drop:]
    for t in entries:
        n += 1
        ts = datetime.fromtimestamp(t.ts / 1000, tz=UTC) + timedelta(hours=tz_hours)
        rows.append(f"{n},{entry},L,{ts:%Y-%m-%d %H:%M},{t.price:.2f},1,0")
        rows.append(f"{n},{exit_},XL,{ts + timedelta(hours=3):%Y-%m-%d %H:%M},{t.price:.2f},1,0")
    return "\n".join(rows), len(entries)


def test_parse_tv_csv_english_and_chinese():
    en = "Trade #,Type,Signal,Date/Time,Price USDT\n1,Entry Long,L,2026-01-02 08:00,100\n1,Exit Long,XL,2026-01-02 12:00,110\n" \
         "2,Entry Short,S,2026-01-03 09:00,105\n"
    tv = parse_tv_trades_csv(en)
    assert [(e.direction, e.ts.hour) for e in tv] == [("long", 8), ("short", 9)]
    zh = "﻿交易 #,類型,訊號,日期/時間,價格\n1,空頭進場,S,2026/01/02 08:00,100\n1,空頭出場,XS,2026/01/02 10:00,90\n"
    assert parse_tv_trades_csv(zh)[0].direction == "short"
    with pytest.raises(ValueError):
        parse_tv_trades_csv("a,b\n1,2\n")


def test_compare_detects_timezone():
    base = datetime(2026, 1, 1)
    ours = [(base + timedelta(hours=h), "long") for h in (5, 20, 44, 70)]
    from goldhunter.tradingview.review import TVEntry

    tv = [TVEntry(ts=t + timedelta(hours=8), direction=d) for t, d in ours]
    rate, off, misses = compare_entries(tv, ours, timedelta(hours=1))
    assert rate == 1.0 and off == 480 and not misses
    tv_wrong = [TVEntry(ts=t + timedelta(hours=8), direction="short") for t, _ in ours]
    assert compare_entries(tv_wrong, ours, timedelta(hours=1))[0] == 0.0


async def test_review_all_pass_with_tv_csv():
    candles = make_candles(900, period=40, amp=15)
    csv_text, n = await tv_csv_from_backtest(candles, tz_hours=8)
    assert n >= 5
    rep = await run_review(ai=FakeAI([EQUIV]), pine=PINE, code=CODE, params={}, inst=BTC_PERP, timeframe="1h",
                           fetch_candles=candles_fetcher(candles), tv_csv=csv_text)
    st = {s.key: s for s in rep.stages}
    assert rep.passed, [(s.key, s.status, s.summary, s.details) for s in rep.stages]
    assert st["tv"].status == "passed" and "UTC+8" in st["tv"].details[0]
    assert rep.metrics["sharpe"] is not None and "sortino" in rep.metrics


async def test_review_fails_when_trades_differ():
    candles = make_candles(900, period=40, amp=15)
    csv_text, _ = await tv_csv_from_backtest(candles, tz_hours=0, lang="zh")
    # 把一半進場改成做空 → 吻合率下降
    lines = csv_text.split("\n")
    flipped = [ln.replace("多頭", "空頭") if i % 4 in (1, 2) else ln for i, ln in enumerate(lines)]
    rep = await run_review(ai=FakeAI([EQUIV]), pine=PINE, code=CODE, params={}, inst=BTC_PERP, timeframe="1h",
                           fetch_candles=candles_fetcher(candles), tv_csv="\n".join(flipped))
    tv = next(s for s in rep.stages if s.key == "tv")
    assert not rep.passed and tv.status == "failed" and "吻合率" in tv.summary


async def test_review_without_csv_can_pass_but_cross_check_can_fail():
    candles = make_candles(600, period=40, amp=15)
    ok = await run_review(ai=FakeAI([EQUIV]), pine=PINE, code=CODE, params={}, inst=BTC_PERP, timeframe="1h",
                          fetch_candles=candles_fetcher(candles))
    assert ok.passed and next(s for s in ok.stages if s.key == "tv").status == "skipped"
    bad = await run_review(ai=FakeAI([NOT_EQUIV]), pine=PINE, code=CODE, params={}, inst=BTC_PERP, timeframe="1h",
                           fetch_candles=candles_fetcher(candles))
    cross = next(s for s in bad.stages if s.key == "cross")
    assert not bad.passed and cross.status == "failed" and "嚴重" in cross.details[0]


async def test_review_unsafe_code_skips_rest():
    rep = await run_review(ai=FakeAI([]), pine=PINE, code="import os\n" + CODE, params={}, inst=BTC_PERP,
                           timeframe="1h", fetch_candles=candles_fetcher(make_candles(300)))
    assert not rep.passed
    assert [s.status for s in rep.stages] == ["failed", "skipped", "skipped", "skipped"]


async def test_smoke_rejects_strategy_without_entries():
    never = CODE.replace("if ta.crossover(f, s) and ctx.position_size == 0:", "if False:")
    rep = await run_review(ai=FakeAI([EQUIV]), pine=PINE, code=never, params={}, inst=BTC_PERP, timeframe="1h",
                           fetch_candles=candles_fetcher(make_candles(600)))
    smoke = next(s for s in rep.stages if s.key == "smoke")
    assert smoke.status == "failed" and "沒有進場" in smoke.summary


async def test_backtest_metrics_complete(candles):
    from goldhunter.strategies.builtin import MACrossStrategy

    r = await run_backtest(MACrossStrategy({"fast": 5, "slow": 20}), BTC_PERP, "1h", candles)
    for k in ("cagr_pct", "excess_return_pct", "max_drawdown_days", "volatility_pct", "sharpe", "sortino",
              "calmar", "payoff_ratio", "expectancy", "max_consecutive_losses", "avg_hold_hours", "exposure_pct"):
        assert k in r.metrics, k
    assert 0 < r.metrics["exposure_pct"] <= 100


async def test_review_handwritten_python_skips_cross_check():
    rep = await run_review(ai=FakeAI([]), pine="", code=CODE, params={}, inst=BTC_PERP, timeframe="1h",
                           fetch_candles=candles_fetcher(make_candles(600, period=40, amp=15)), exchange_id="okx")
    cross = next(s for s in rep.stages if s.key == "cross")
    assert rep.passed and cross.status == "skipped" and rep.exchange_id == "okx"
