"""回測引擎：與實盤共用同一套 Strategy / 風控 / 撮合邏輯。

避免未來函數：第 i 根收盤後運算策略，於第 i+1 根「開盤價」成交；
止損 / 止盈用第 i+1 根的高低價判斷（跳空時以開盤價成交）。
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime

from pydantic import BaseModel

from goldhunter.core.models import Action, Candle, Decision, Instrument, OrderRequest, Side
from goldhunter.exchanges.paper import PaperExchange
from goldhunter.risk.manager import RiskConfig, RiskState, evaluate
from goldhunter.strategies.base import Strategy, StrategyContext

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
              "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400, "1w": 604800}


class BacktestConfig(BaseModel):
    initial_cash: float = 10_000.0
    fee_rate: float = 0.0005
    slippage: float = 0.0005
    risk: RiskConfig = RiskConfig(daily_loss_limit_pct=0, max_orders_per_hour=10_000)
    max_ai_calls: int = 200  # AI 策略回測會產生費用，設上限


class BacktestTrade(BaseModel):
    ts: int
    side: str
    quantity: float
    price: float
    fee: float
    reduce_only: bool
    realized_pnl: float | None = None
    reason: str = ""


class BacktestResult(BaseModel):
    metrics: dict
    equity_curve: list[tuple[int, float]]
    trades: list[BacktestTrade]
    decisions: int
    rejected: list[str]


async def run_backtest(strategy: Strategy, instrument: Instrument, timeframe: str, candles: list[Candle],
                       config: BacktestConfig | None = None) -> BacktestResult:
    cfg = config or BacktestConfig()
    ex = PaperExchange(initial_cash=cfg.initial_cash, fee_rate=cfg.fee_rate, slippage=cfg.slippage)
    state = RiskState()
    trades: list[BacktestTrade] = []
    curve: list[tuple[int, float]] = []
    rejected: list[str] = []
    stops: dict = {}
    pending: Decision | None = None
    n_decisions = ai_calls = 0
    bars_in_market = 0

    async def execute(req: OrderRequest, price: float, ts: int, reason: str):
        ex.slippage = cfg.slippage
        ex.set_price(instrument, price)
        pos = ex.positions.get(instrument)
        entry, qty0 = (pos.entry_price, pos.quantity) if pos else (None, 0.0)
        o = await ex.place_order(req)
        if o.status.value != "filled":
            rejected.append(f"{_iso(ts)} 下單被拒：{(o.raw or {}).get('reason')}")
            return
        pnl = None
        if req.reduce_only and entry is not None and o.avg_price is not None:
            pnl = (o.avg_price - entry) * o.filled * (1 if qty0 > 0 else -1) - o.fee
        trades.append(BacktestTrade(ts=ts, side=req.side.value, quantity=o.filled, price=o.avg_price or price,
                                    fee=o.fee, reduce_only=req.reduce_only, realized_pnl=pnl, reason=reason))

    for i in range(len(candles)):
        if i and i % 2000 == 0:
            await asyncio.sleep(0)  # 長回測時讓出事件迴圈，運行中的 Bot 不會卡住
        bar = candles[i]
        # 1) 以本根開盤價執行上一根產生的決策
        if pending is not None:
            ex.set_price(instrument, bar.open)
            pos = ex.positions.get(instrument)
            res = evaluate(pending, config=cfg.risk, state=state, equity=ex.equity(), price=bar.open,
                           position=pos, all_positions=list(ex.positions.values()),
                           capabilities=ex.capabilities(instrument), round_qty=ex.round_qty,
                           now=bar.ts / 1000)
            if res.approved:
                state.record_orders(len(res.orders), now=bar.ts / 1000)
                for req in res.orders:
                    await execute(req, bar.open, bar.ts, pending.reasoning)
                adj = res.adjusted or pending
                if adj.action in (Action.OPEN_LONG, Action.OPEN_SHORT) and instrument in ex.positions:
                    stops = {"sl": adj.stop_loss, "tp": adj.take_profit}
                elif instrument not in ex.positions:
                    stops = {}
            elif pending.action != Action.HOLD:
                rejected.append(f"{_iso(bar.ts)} {pending.action.value}：{'；'.join(res.reasons)}")
            pending = None

        # 2) 本根 K 棒內的止損 / 止盈
        pos = ex.positions.get(instrument)
        if pos and stops:
            long = pos.quantity > 0
            sl, tp = stops.get("sl"), stops.get("tp")
            fill, why = None, ""
            if sl is not None and ((long and bar.low <= sl) or (not long and bar.high >= sl)):
                fill, why = (min(bar.open, sl) if long else max(bar.open, sl)), "止損"
            elif tp is not None and ((long and bar.high >= tp) or (not long and bar.low <= tp)):
                fill, why = (max(bar.open, tp) if long else min(bar.open, tp)), "止盈"
            if fill is not None:
                req = OrderRequest(instrument=instrument, side=Side.SELL if long else Side.BUY,
                                   quantity=abs(pos.quantity), reduce_only=True)
                await execute(req, fill, bar.ts, why)
                stops = {}

        # 3) 收盤後運算策略
        ex.set_price(instrument, bar.close)
        window = candles[max(0, i - 499) : i + 1]
        if i + 1 < len(candles) and len(window) >= strategy.warmup:
            if strategy.uses_ai:
                if ai_calls >= cfg.max_ai_calls:
                    window = []
                ai_calls += 1
            if window:
                ctx = StrategyContext(
                    instrument=instrument, timeframe=timeframe, candles=window,
                    position=ex.positions.get(instrument), balance=await ex.fetch_balance(),
                    capabilities=ex.capabilities(instrument), max_leverage=cfg.risk.max_leverage,
                    max_size_pct=cfg.risk.max_position_pct,
                )
                d = await strategy.run(ctx)
                if d is not None and d.action != Action.HOLD:
                    pending, n_decisions = d, n_decisions + 1
        curve.append((bar.ts, ex.equity()))
        if ex.positions:
            bars_in_market += 1

    metrics = compute_metrics(curve, trades, candles, timeframe, cfg.initial_cash)
    if candles:
        metrics["exposure_pct"] = round(bars_in_market / len(candles) * 100, 1)
    return BacktestResult(metrics=metrics,
                          equity_curve=curve, trades=trades, decisions=n_decisions, rejected=rejected[:200])


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def compute_metrics(curve, trades: list[BacktestTrade], candles: list[Candle], timeframe: str,
                    initial: float) -> dict:
    """量化策略常用績效指標（所有百分比已乘 100）"""
    if not curve:
        return {}
    equities = [e for _, e in curve]
    final = equities[-1]
    tf_sec = TF_SECONDS.get(timeframe, 86400)
    periods_per_year = 365 * 86400 / tf_sec

    # 回撤與最長回撤期間
    peak, peak_i, max_dd, longest = equities[0], 0, 0.0, 0
    for i, e in enumerate(equities):
        if e >= peak:
            peak, peak_i = e, i
        else:
            max_dd = max(max_dd, (peak - e) / peak if peak else 0)
            longest = max(longest, i - peak_i)

    # 報酬率序列 → 波動率、Sharpe、Sortino
    rets = [(b - a) / a for a, b in zip(equities, equities[1:]) if a]
    sharpe = sortino = vol = 0.0
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        downside = math.sqrt(sum(min(r, 0) ** 2 for r in rets) / len(rets))
        vol = sd * math.sqrt(periods_per_year)
        sharpe = mean / sd * math.sqrt(periods_per_year) if sd else 0.0
        sortino = mean / downside * math.sqrt(periods_per_year) if downside else 0.0

    years = max(len(equities) * tf_sec / (365 * 86400), 1e-9)
    total_ret = final / initial - 1
    cagr = (final / initial) ** (1 / years) - 1 if final > 0 and years >= 1 / 365 else 0.0
    calmar = cagr / max_dd if max_dd else None

    # 逐筆交易（以平倉紀錄計算損益；持倉時間以開倉到平倉）
    closed = [t.realized_pnl for t in trades if t.realized_pnl is not None]
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    streak = max_streak = 0
    for p in closed:
        streak = streak + 1 if p <= 0 else 0
        max_streak = max(max_streak, streak)
    holds, open_ts = [], None
    for t in trades:
        if not t.reduce_only:
            open_ts = open_ts or t.ts
        elif open_ts is not None:
            holds.append(t.ts - open_ts)
            open_ts = None
    bh = (candles[-1].close / candles[0].close - 1) * 100 if candles and candles[0].close else 0.0

    return {
        # 報酬
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "buy_and_hold_pct": round(bh, 2),
        "excess_return_pct": round(total_ret * 100 - bh, 2),
        # 風險
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_bars": longest,
        "max_drawdown_days": round(longest * tf_sec / 86400, 1),
        "volatility_pct": round(vol * 100, 2),
        # 風險調整後報酬
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2) if calmar is not None else None,
        # 交易品質
        "trades": len(trades),
        "closed_trades": len(closed),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "expectancy": round(sum(closed) / len(closed), 2) if closed else 0.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(-avg_loss, 2),
        "best_trade": round(max(closed), 2) if closed else None,
        "worst_trade": round(min(closed), 2) if closed else None,
        "max_consecutive_losses": max_streak,
        # 效率與成本
        "avg_hold_hours": round(sum(holds) / len(holds) / 3_600_000, 1) if holds else None,
        "total_fees": round(sum(t.fee for t in trades), 2),
        "bars": len(candles),
        "days": round(years * 365, 1),
    }
