"""進場分析 API：回測頁（任一策略 × 標的）與監控頁（運行中 Bot 的每個標的）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from goldhunter.analysis.entry import analyze_entry, strategy_signal_indices, trend_direction
from goldhunter.backtest.data import fetch_history
from goldhunter.backtest.engine import TF_SECONDS
from goldhunter.core.models import Action, Balance, Instrument
from goldhunter.engine.manager import manager, provider_from_config
from goldhunter.exchanges.base import Capabilities
from goldhunter.personas.attach import attach_personas
from goldhunter.store.db import AIModelConfig, DecisionLog, StrategyConfig, get_session
from goldhunter.strategies.base import StrategyContext
from goldhunter.strategies.registry import attach_reference, build_strategy

router = APIRouter()


class EntryIn(BaseModel):
    strategy_id: int
    exchange_id: str = "binance"
    symbol: str = "crypto:BTC/USDT:perp"
    timeframe: str = "1h"
    params: dict[str, Any] | None = None
    direction: str | None = None  # 目前沒有訊號時，可指定方向做假設分析
    ai_model_id: int | None = None  # AI 交易員要實際問一次 AI（會產生費用）


def _signal_out(d) -> dict | None:
    if d is None:
        return None
    return {"action": d.action.value, "stop_loss": d.stop_loss, "take_profit": d.take_profit,
            "size_pct": d.size_pct, "reasoning": d.reasoning, "confidence": d.confidence}


@router.post("/analysis/entry")
async def entry_for_strategy(body: EntryIn, s: Session = Depends(get_session)):
    st = s.get(StrategyConfig, body.strategy_id)
    if not st:
        raise HTTPException(404, "策略不存在")
    if st.kind == "tradingview":
        raise HTTPException(400, "TradingView 訊號策略由 TradingView 產生訊號，請指定方向做假設分析")
    try:
        inst = Instrument.parse(body.symbol)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    ai = None
    if body.ai_model_id:
        m = s.get(AIModelConfig, body.ai_model_id)
        if not m:
            raise HTTPException(404, "AI 模型不存在")
        ai = provider_from_config(m)
    strategy = build_strategy(st.kind, {**(st.params or {}), **(body.params or {})}, st.code, ai=ai)
    attach_reference(strategy, s, check=False)
    attach_personas(strategy, s, check=False)
    tf = TF_SECONDS.get(body.timeframe, 3600)
    end = datetime.now(UTC)
    try:
        candles = await fetch_history(body.exchange_id, inst, body.timeframe,
                                      int((end - timedelta(seconds=tf * 1500)).timestamp() * 1000),
                                      int(end.timestamp() * 1000))
    except Exception as e:
        raise HTTPException(502, f"下載歷史資料失敗：{type(e).__name__}: {e}") from e
    if len(candles) < 80:
        raise HTTPException(400, "歷史資料不足")
    closed = candles[:-1]
    signal = None
    if not strategy.uses_ai or ai is not None:
        ctx = StrategyContext(instrument=inst, timeframe=body.timeframe, candles=closed, position=None,
                              balance=Balance(currency="USDT", total=10_000, free=10_000),
                              capabilities=Capabilities(supports_short=True, max_leverage=125), max_leverage=3)
        signal = await strategy.run(ctx)
    direction, assumed = body.direction, None
    if signal is not None and signal.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
        direction = "long" if signal.action == Action.OPEN_LONG else "short"
    elif not direction:
        direction, assumed = trend_direction(closed)  # 沒有訊號：依趨勢假設方向，不讓畫面空白
    sig_idx = await strategy_signal_indices(build_strategy(st.kind, strategy.params, st.code), inst,
                                            body.timeframe, closed, direction)
    is_sig = signal is not None and signal.action in (Action.OPEN_LONG, Action.OPEN_SHORT)
    analysis = analyze_entry(closed, body.timeframe, direction, signal.stop_loss if is_sig else None,
                             signal.take_profit if is_sig else None, sig_idx)
    return {"signal": _signal_out(signal), "analysis": analysis.model_dump(),
            "hypothetical": not is_sig, "assumed_reason": assumed,
            "note": None if is_sig else (assumed or f"目前沒有進場訊號，以下是假設現在{'做多' if direction == 'long' else '做空'}的分析")}


@router.get("/bots/{bot_id}/entry-analysis")
async def entry_for_bot(bot_id: int, direction: str | None = None, s: Session = Depends(get_session)):
    """運行中 Bot 的每個標的：目前訊號（或 AI 最近一次開倉判斷）、進場分析、等待中的掛單"""
    runner = manager.runners.get(bot_id)
    if not runner or not runner.running:
        raise HTTPException(400, "Bot 未啟動")
    out = []
    since = datetime.now(UTC) - timedelta(seconds=TF_SECONDS.get(runner.timeframe, 3600) * 3)
    for inst in runner.instruments:
        item: dict = {"instrument": str(inst), "signal": None, "analysis": None, "pending": None}
        pe = runner.pending.get(inst)
        if pe:
            item["pending"] = {"level": pe.level, "label": pe.label, "bars_left": pe.bars_left,
                               "action": pe.decision.action.value}
        try:
            candles = (await runner.exchange.fetch_candles(inst, runner.timeframe, 1000))[:-1]
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"
            out.append(item)
            continue
        signal = None
        if runner.strategy is not None and not runner.strategy.uses_ai and len(candles) >= runner.strategy.warmup:
            pos = next((p for p in await runner.exchange.fetch_positions() if p.instrument == inst), None)
            ctx = StrategyContext(instrument=inst, timeframe=runner.timeframe, candles=candles, position=pos,
                                  balance=await runner.exchange.fetch_balance(),
                                  capabilities=runner.exchange.capabilities(inst),
                                  max_leverage=runner.risk.max_leverage, max_size_pct=runner.risk.max_position_pct)
            probe = type(runner.strategy)(dict(runner.strategy.params))
            signal = await probe.run(ctx)
            item["signal"] = _signal_out(signal)
        elif runner.strategy is not None and runner.strategy.uses_ai:
            last = s.exec(select(DecisionLog).where(
                DecisionLog.bot_id == bot_id, DecisionLog.instrument == str(inst),
                col(DecisionLog.action).in_(["open_long", "open_short"]), DecisionLog.ts >= since,
            ).order_by(col(DecisionLog.ts).desc())).first()
            if last:
                item["signal"] = {"action": last.action, "stop_loss": last.decision.get("stop_loss"),
                                  "take_profit": last.decision.get("take_profit"),
                                  "reasoning": last.decision.get("reasoning"), "from_log": True}
        sig = item["signal"]
        d = direction
        if sig and sig["action"] in ("open_long", "open_short"):
            d = "long" if sig["action"] == "open_long" else "short"
        elif not d and len(candles) >= 60:
            d, item["assumed_reason"] = trend_direction(candles)  # 沒有訊號：依趨勢假設方向
        if d and len(candles) >= 60:
            idx = []
            if runner.strategy is not None and not runner.strategy.uses_ai:
                idx = await strategy_signal_indices(type(runner.strategy)(dict(runner.strategy.params)), inst,
                                                    runner.timeframe, candles, d)
            is_sig = bool(sig and sig["action"] in ("open_long", "open_short"))
            item["analysis"] = analyze_entry(candles, runner.timeframe, d,
                                             sig.get("stop_loss") if is_sig else None,
                                             sig.get("take_profit") if is_sig else None, idx).model_dump()
            item["hypothetical"] = not is_sig
        out.append(item)
    return {"entry": runner.entry.model_dump(), "items": out}
