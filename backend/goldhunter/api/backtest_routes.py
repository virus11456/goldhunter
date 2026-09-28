from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from goldhunter.backtest.data import fetch_history
from goldhunter.backtest.engine import BacktestConfig, run_backtest
from goldhunter.core.models import Instrument
from goldhunter.engine.manager import provider_from_config
from goldhunter.risk.manager import RiskConfig
from goldhunter.store.db import AIModelConfig, BacktestRun, StrategyConfig, get_session
from goldhunter.strategies.registry import attach_reference, build_strategy

router = APIRouter()


class BacktestIn(BaseModel):
    strategy_id: int
    params: dict[str, Any] | None = None  # 覆寫策略參數（調參用）
    ai_model_id: int | None = None
    exchange_id: str = "binance"
    symbol: str  # Instrument 格式，如 crypto:BTC/USDT:perp
    timeframe: str = "1h"
    start: datetime
    end: datetime
    initial_cash: float = 10_000
    fee_rate: float = 0.0005
    slippage: float = 0.0005
    risk: dict[str, Any] = {}
    max_ai_calls: int = 200


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


@router.post("/backtests")
async def create_backtest(body: BacktestIn, s: Session = Depends(get_session)):
    st = s.get(StrategyConfig, body.strategy_id)
    if not st:
        raise HTTPException(404, "策略不存在")
    if st.kind == "tradingview":
        raise HTTPException(400, "TradingView 訊號策略無法回測，請先把 Pine Script 轉成 Python 策略")
    ai = None
    if body.ai_model_id:
        m = s.get(AIModelConfig, body.ai_model_id)
        if not m:
            raise HTTPException(404, "AI 模型不存在")
        ai = provider_from_config(m)
    try:
        inst = Instrument.parse(body.symbol)
        params = {**(st.params or {}), **(body.params or {})}
        strategy = build_strategy(st.kind, params, st.code, ai=ai)
        attach_reference(strategy, s)
        risk = RiskConfig(**{"daily_loss_limit_pct": 0, "max_orders_per_hour": 10_000, **body.risk})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if strategy.uses_ai and ai is None:
        raise HTTPException(400, "AI 策略回測需要選擇 AI 模型（注意：會產生 API 費用）")
    try:
        candles = await fetch_history(body.exchange_id, inst, body.timeframe, _ms(body.start), _ms(body.end))
    except Exception as e:
        raise HTTPException(502, f"下載歷史資料失敗：{type(e).__name__}: {e}") from e
    if len(candles) < strategy.warmup + 2:
        raise HTTPException(400, f"歷史資料不足（{len(candles)} 根），至少需要 {strategy.warmup + 2} 根")
    cfg = BacktestConfig(initial_cash=body.initial_cash, fee_rate=body.fee_rate, slippage=body.slippage,
                         risk=risk, max_ai_calls=body.max_ai_calls)
    result = await run_backtest(strategy, inst, body.timeframe, candles, cfg)
    run = BacktestRun(
        strategy_id=st.id, strategy_name=st.name, instrument=body.symbol, timeframe=body.timeframe,
        config=body.model_dump(mode="json"), metrics=result.metrics,
        equity_curve=[[ts, round(eq, 4)] for ts, eq in result.equity_curve[:: max(1, len(result.equity_curve) // 1000)]],
        trades=[t.model_dump() for t in result.trades],
    )
    s.add(run)
    s.commit()
    s.refresh(run)
    out = run.model_dump()
    out["rejected"] = result.rejected
    out["candles"] = [[c.ts, c.open, c.high, c.low, c.close] for c in candles[:: max(1, len(candles) // 1000)]]
    return out


@router.get("/backtests")
def list_backtests(s: Session = Depends(get_session)):
    rows = s.exec(select(BacktestRun).order_by(col(BacktestRun.created_at).desc()).limit(100)).all()
    return [{k: v for k, v in r.model_dump().items() if k not in ("equity_curve", "trades")} for r in rows]


@router.get("/backtests/{run_id}")
def get_backtest(run_id: int, s: Session = Depends(get_session)):
    r = s.get(BacktestRun, run_id)
    if not r:
        raise HTTPException(404, "找不到回測")
    return r.model_dump()


@router.delete("/backtests/{run_id}")
def delete_backtest(run_id: int, s: Session = Depends(get_session)):
    r = s.get(BacktestRun, run_id)
    if r:
        s.delete(r)
        s.commit()
    return {"ok": True}
