"""Bot 管理、即時監控（運行中的 Bot、持倉、成交紀錄、AI 決策紀錄）、總覽與緊急全停。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from goldhunter.core.models import Action, Decision, Instrument
from goldhunter.engine.manager import manager
from goldhunter.risk.manager import RiskConfig
from goldhunter.store.db import Bot, DecisionLog, EquitySnapshot, StrategyConfig, Trade, get_session

router = APIRouter()


class BotIn(BaseModel):
    name: str
    account_id: int
    strategy_id: int
    ai_model_id: int | None = None
    symbols: list[str]
    timeframe: str = "15m"
    interval_sec: int = 60
    risk: dict[str, Any] = {}


def bot_out(b: Bot, s: Session) -> dict:
    runner = manager.runners.get(b.id)  # type: ignore[arg-type]
    strat = s.get(StrategyConfig, b.strategy_id)
    d = b.model_dump()
    d["strategy_name"] = strat.name if strat else None
    d["strategy_kind"] = strat.kind if strat else None
    d["running"] = bool(runner and runner.running)
    if runner:
        d["last_error"] = runner.last_error or b.last_error
        d["last_run_at"] = runner.last_run_at
        d["halted_reason"] = runner.risk_state.halted_reason
    eq = s.exec(select(EquitySnapshot).where(EquitySnapshot.bot_id == b.id)
                .order_by(col(EquitySnapshot.ts).desc())).first()
    d["equity"] = eq.equity if eq else None
    return d


def _validate(body: BotIn) -> None:
    try:
        for sym in body.symbols:
            Instrument.parse(sym)
        RiskConfig(**body.risk)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not body.symbols:
        raise HTTPException(400, "至少需要一個交易對")


@router.get("/bots")
def list_bots(s: Session = Depends(get_session)):
    return [bot_out(b, s) for b in s.exec(select(Bot))]


@router.post("/bots")
def create_bot(body: BotIn, s: Session = Depends(get_session)):
    _validate(body)
    b = Bot(**body.model_dump(), risk=RiskConfig(**body.risk).model_dump())
    s.add(b)
    s.commit()
    s.refresh(b)
    return bot_out(b, s)


@router.put("/bots/{bot_id}")
def update_bot(bot_id: int, body: BotIn, s: Session = Depends(get_session)):
    b = s.get(Bot, bot_id) or _404()
    if manager.runners.get(bot_id):
        raise HTTPException(400, "請先停止 Bot 再修改設定")
    _validate(body)
    for k, v in body.model_dump().items():
        setattr(b, k, v)
    b.risk = RiskConfig(**body.risk).model_dump()
    s.add(b)
    s.commit()
    return bot_out(b, s)


@router.delete("/bots/{bot_id}")
async def delete_bot(bot_id: int, s: Session = Depends(get_session)):
    b = s.get(Bot, bot_id) or _404()
    await manager.stop(bot_id)
    s.delete(b)
    s.commit()
    return {"ok": True}


@router.post("/bots/{bot_id}/start")
async def start_bot(bot_id: int):
    try:
        await manager.start(bot_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@router.post("/bots/{bot_id}/stop")
async def stop_bot(bot_id: int):
    await manager.stop(bot_id)
    return {"ok": True}


@router.post("/bots/stop-all")
async def stop_all():
    """緊急全停：停止所有 Bot（不會自動平倉，持倉請自行決定是否平倉）"""
    return {"stopped": await manager.stop_all()}


@router.post("/bots/{bot_id}/regenerate-webhook-secret")
def regen_secret(bot_id: int, s: Session = Depends(get_session)):
    import secrets

    b = s.get(Bot, bot_id) or _404()
    b.webhook_secret = secrets.token_urlsafe(16)
    s.add(b)
    s.commit()
    return {"webhook_secret": b.webhook_secret}


@router.get("/bots/{bot_id}/positions")
async def bot_positions(bot_id: int):
    runner = manager.runners.get(bot_id)
    if not runner:
        return {"running": False, "positions": [], "balance": None}
    positions = await runner.exchange.fetch_positions()
    balance = await runner.exchange.fetch_balance()
    out = []
    for p in positions:
        d = p.model_dump(mode="json")
        d["instrument"] = str(p.instrument)
        d["side"] = p.side
        d["mark_price"] = runner.last_price.get(p.instrument)
        lv = runner.stops.get(p.instrument)
        d["stop_loss"], d["take_profit"] = (lv.stop_loss, lv.take_profit) if lv else (None, None)
        out.append(d)
    return {"running": runner.running, "positions": out, "balance": balance.model_dump()}


class ManualSignal(BaseModel):
    instrument: str
    action: Action
    size_pct: float = 10
    leverage: int = 1
    stop_loss: float | None = None
    take_profit: float | None = None


@router.post("/bots/{bot_id}/signal")
async def manual_signal(bot_id: int, body: ManualSignal):
    """手動下單（同樣經過風控），例如在監控頁一鍵平倉"""
    runner = manager.runners.get(bot_id)
    if not runner or not runner.running:
        raise HTTPException(400, "Bot 未啟動")
    d = Decision(instrument=Instrument.parse(body.instrument), action=body.action, size_pct=body.size_pct,
                 leverage=body.leverage, stop_loss=body.stop_loss, take_profit=body.take_profit,
                 reasoning="手動操作", source="manual")
    entry = await runner.handle_decision(d)
    return entry.model_dump()


# ------------------------------ 紀錄查詢 ------------------------------
@router.get("/trades")
def list_trades(bot_id: int | None = None, limit: int = 200, s: Session = Depends(get_session)):
    q = select(Trade).order_by(col(Trade.ts).desc()).limit(min(limit, 2000))
    if bot_id:
        q = q.where(Trade.bot_id == bot_id)
    return list(s.exec(q))


@router.get("/decisions")
def list_decisions(bot_id: int | None = None, limit: int = 100, s: Session = Depends(get_session)):
    q = select(DecisionLog).order_by(col(DecisionLog.ts).desc()).limit(min(limit, 1000))
    if bot_id:
        q = q.where(DecisionLog.bot_id == bot_id)
    return list(s.exec(q))


@router.get("/equity")
def equity_curve(bot_id: int, hours: int = 168, s: Session = Depends(get_session)):
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = s.exec(select(EquitySnapshot).where(EquitySnapshot.bot_id == bot_id, EquitySnapshot.ts >= since)
                  .order_by(col(EquitySnapshot.ts))).all()
    step = max(1, len(rows) // 500)  # 最多回傳約 500 點
    return [{"ts": r.ts, "equity": r.equity} for r in rows[::step]]


@router.get("/dashboard")
def dashboard(s: Session = Depends(get_session)):
    bots = [bot_out(b, s) for b in s.exec(select(Bot))]
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    realized_today = s.exec(select(func.coalesce(func.sum(Trade.realized_pnl), 0.0)).where(Trade.ts >= today)).one()
    trades_today = s.exec(select(func.count()).select_from(Trade).where(Trade.ts >= today)).one()
    return {
        "bots_total": len(bots),
        "bots_running": sum(1 for b in bots if b["running"]),
        "total_equity": round(sum(b["equity"] or 0 for b in bots), 2),
        "realized_pnl_today": round(float(realized_today or 0), 2),
        "trades_today": trades_today,
        "bots": bots,
    }


def _404(msg: str = "找不到資料"):
    raise HTTPException(404, msg)
