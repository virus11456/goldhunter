"""Bot 管理、即時監控（運行中的 Bot、持倉、成交紀錄、AI 決策紀錄）、總覽與緊急全停。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from goldhunter.copilot.config import CopilotConfig
from goldhunter.core.models import Action, Decision, Instrument
from goldhunter.engine.manager import manager
from goldhunter.engine.universe import UniverseRules
from goldhunter.risk.manager import RiskConfig
from goldhunter.store.db import Bot, DecisionLog, EquitySnapshot, StrategyConfig, Trade, TuningRun, get_session

router = APIRouter()


class AITraderIn(BaseModel):
    """直接建立 AI 交易員（不需先建立策略）"""

    instructions: str = ""
    reference_strategy_id: int | None = None
    min_confidence: float = 0.6
    persona_id: int | None = None  # 交易大腦（投資大師）
    reviewer_ids: list[int] = []  # 審查委員（投資大師）
    veto_rule: str = "any"  # any / majority


class BotIn(BaseModel):
    name: str
    account_id: int
    strategy_id: int | None = None
    ai_trader: AITraderIn | None = None
    ai_model_id: int | None = None
    symbols: list[str]
    timeframe: str = "15m"
    interval_sec: int = 60
    risk: dict[str, Any] = {}
    copilot: dict[str, Any] = {}
    params_override: dict[str, Any] = {}
    universe: dict[str, Any] = {}


def bot_out(b: Bot, s: Session) -> dict:
    runner = manager.runners.get(b.id)  # type: ignore[arg-type]
    strat = s.get(StrategyConfig, b.strategy_id)
    d = b.model_dump()
    d["strategy_name"] = strat.name if strat else None
    d["strategy_kind"] = strat.kind if strat else None
    d["mode"] = "ai_trader" if strat and strat.kind == "ai" else ("tradingview" if strat and strat.kind == "tradingview"
                                                                   else "strategy")
    d["ai_trader"] = (
        {k: (strat.params or {}).get(k) for k in ("instructions", "reference_strategy_id", "min_confidence",
                                                  "persona_id", "reviewer_ids", "veto_rule")}
        if strat and strat.kind == "ai" else None
    )
    d["running"] = bool(runner and runner.running)
    d["copilot"] = CopilotConfig(**(b.copilot or {})).model_dump()
    cp = d["copilot"]
    # 設定上有開啟 AI 副駕駛（不論是否運行中）；copilot_active＝目前正在運作
    d["copilot_enabled"] = bool(b.ai_model_id and (cp["review"] or cp["manage"] or cp["tune"]))
    d["copilot_active"] = bool(runner and runner.copilot_active)
    d["halted_reason"] = runner.risk_state.halted_reason if runner else None
    if runner:
        d["last_error"] = runner.last_error or b.last_error
        d["last_run_at"] = runner.last_run_at or b.last_run_at
    eq = s.exec(select(EquitySnapshot).where(EquitySnapshot.bot_id == b.id)
                .order_by(col(EquitySnapshot.ts).desc())).first()
    d["equity"] = eq.equity if eq else None
    d["baseline_equity"] = eq.baseline_equity if eq else None
    return d


def _ensure_strategy(body: BotIn, s: Session, existing: Bot | None = None) -> None:
    """ai_trader 模式：建立（或更新此 Bot 專屬的）AI 交易員策略"""
    if body.ai_trader is None:
        if not body.strategy_id:
            raise HTTPException(400, "請選擇策略，或使用 AI 交易員模式")
        return
    if not body.ai_model_id:
        raise HTTPException(400, "AI 交易員需要選擇 AI 模型")
    from goldhunter.strategies.ai_strategy import AIStrategy

    params = {**AIStrategy.default_params, **body.ai_trader.model_dump(exclude_none=False)}
    if not params["instructions"]:
        params["instructions"] = AIStrategy.default_params["instructions"]
    st = s.get(StrategyConfig, existing.strategy_id) if existing else None
    if not (st and st.kind == "ai" and st.name.startswith("AI 交易員｜")):
        st = StrategyConfig(name=f"AI 交易員｜{body.name}", kind="ai")
    st.params, st.status = params, "active"
    s.add(st)
    s.commit()
    s.refresh(st)
    body.strategy_id = st.id


def _validate(body: BotIn) -> None:
    try:
        for sym in body.symbols:
            Instrument.parse(sym)
        RiskConfig(**body.risk)
        CopilotConfig(**body.copilot)
        UniverseRules(**body.universe)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not body.symbols and (body.universe or {}).get("mode") != "rules":
        raise HTTPException(400, "至少需要一個交易對（或改用規則自動挑選）")
    if body.ai_trader and body.ai_trader.veto_rule not in ("any", "majority"):
        raise HTTPException(400, "否決規則只能是 any 或 majority")


@router.get("/bots")
def list_bots(s: Session = Depends(get_session)):
    return [bot_out(b, s) for b in s.exec(select(Bot))]


@router.post("/bots")
def create_bot(body: BotIn, s: Session = Depends(get_session)):
    _validate(body)
    _ensure_strategy(body, s)
    b = Bot(**body.model_dump(exclude={"risk", "copilot", "ai_trader", "universe"}), risk=RiskConfig(**body.risk).model_dump(),
            universe=UniverseRules(**body.universe).model_dump() if body.universe else {},
            copilot=CopilotConfig(**body.copilot).model_dump())
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
    _ensure_strategy(body, s, existing=b)
    for k, v in body.model_dump(exclude={"ai_trader", "universe"}).items():
        setattr(b, k, v)
    b.universe = UniverseRules(**body.universe).model_dump() if body.universe else {}
    b.risk = RiskConfig(**body.risk).model_dump()
    b.copilot = CopilotConfig(**body.copilot).model_dump()
    s.add(b)
    s.commit()
    s.refresh(b)
    return bot_out(b, s)


@router.delete("/bots/{bot_id}")
async def delete_bot(bot_id: int, s: Session = Depends(get_session)):
    b = s.get(Bot, bot_id) or _404()
    await manager.stop(bot_id)
    for model in (Trade, DecisionLog, EquitySnapshot, TuningRun):
        for row in s.exec(select(model).where(model.bot_id == bot_id)):
            s.delete(row)
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
def list_decisions(bot_id: int | None = None, limit: int = 100, hide_hold: bool = False,
                   s: Session = Depends(get_session)):
    q = select(DecisionLog).order_by(col(DecisionLog.ts).desc()).limit(min(limit, 1000))
    if bot_id:
        q = q.where(DecisionLog.bot_id == bot_id)
    if hide_hold:
        q = q.where(DecisionLog.action != "hold")
    return list(s.exec(q))


@router.get("/equity")
def equity_curve(bot_id: int, hours: int = 168, s: Session = Depends(get_session)):
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = s.exec(select(EquitySnapshot).where(EquitySnapshot.bot_id == bot_id, EquitySnapshot.ts >= since)
                  .order_by(col(EquitySnapshot.ts))).all()
    step = max(1, len(rows) // 500)  # 最多回傳約 500 點
    return [{"ts": r.ts, "equity": r.equity, "baseline_equity": r.baseline_equity} for r in rows[::step]]


@router.get("/dashboard")
def dashboard(s: Session = Depends(get_session)):
    bots = [bot_out(b, s) for b in s.exec(select(Bot))]
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
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


# ------------------------------ AI 參數微調 ------------------------------
@router.get("/tuning")
def list_tuning(bot_id: int | None = None, s: Session = Depends(get_session)):
    q = select(TuningRun).order_by(col(TuningRun.ts).desc()).limit(50)
    if bot_id:
        q = q.where(TuningRun.bot_id == bot_id)
    return list(s.exec(q))


@router.post("/bots/{bot_id}/tune-now")
async def tune_now(bot_id: int):
    runner = manager.runners.get(bot_id)
    if not runner or not runner.running:
        raise HTTPException(400, "Bot 未啟動")
    if not (runner.ai and runner.strategy and not runner.strategy.uses_ai):
        raise HTTPException(400, "需要規則型 / 自訂策略並設定 AI 模型")
    return await runner.run_tune()


@router.post("/tuning/{run_id}/apply")
def apply_tuning(run_id: int, s: Session = Depends(get_session)):
    run = s.get(TuningRun, run_id) or _404()
    if run.status not in ("proposed", "rejected") or not run.proposed_params:
        raise HTTPException(400, f"此建議狀態為 {run.status}，無法套用")
    runner = manager.runners.get(run.bot_id)
    if runner and runner.strategy:
        runner.apply_params(run.proposed_params)
    else:
        bot = s.get(Bot, run.bot_id) or _404("Bot 不存在")
        st = s.get(StrategyConfig, bot.strategy_id)
        base = dict(st.params or {}) if st else {}
        merged = {**base, **(bot.params_override or {}), **run.proposed_params}
        bot.params_override = {k: v for k, v in merged.items() if base.get(k) != v}
        s.add(bot)
    run.status = "applied"
    s.add(run)
    s.commit()
    s.refresh(run)
    return run


def _404(msg: str = "找不到資料"):
    raise HTTPException(404, msg)
