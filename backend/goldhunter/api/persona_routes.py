"""投資大師（只接受女媧 nuwa-skill 蒸餾好的檔案）與大師組合。"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from goldhunter.ai.base import AIProviderError
from goldhunter.engine.manager import manager, provider_from_config
from goldhunter.personas.lifecycle import PASS_SCORE, STATUS_LABEL, _bots_using, maybe_promote, persona_progress
from goldhunter.personas.nuwa import parse_nuwa
from goldhunter.personas.portfolio import PortfolioPlan, design_plan, plan_to_bot_fields
from goldhunter.risk.manager import RiskConfig
from goldhunter.store.db import (
    AIModelConfig,
    Bot,
    EquitySnapshot,
    ExchangeAccount,
    Persona,
    StrategyConfig,
    Trade,
    get_session,
)

router = APIRouter()
MIN_SAMPLE_DAYS, MIN_SAMPLE_TRADES = 14, 30  # 大師組合績效至少要這麼多樣本才有比較意義


def persona_out(p: Persona, s: Session, full: bool = False) -> dict:
    d = p.model_dump(exclude=set() if full else {"profile"})
    if not full:
        d["meta"] = {k: v for k, v in (p.meta or {}).items() if k != "raw_skill"}
    d["status_label"] = STATUS_LABEL.get(p.status, p.status)
    if p.status == "draft" and not (p.fidelity or {}).get("score"):
        d["status_label"] = "未附保真度評分"
    d["paper_progress"] = persona_progress(p, s)
    d["profile_chars"] = len(p.profile or "")
    d["used_by_bots"] = len(_bots_using(p, s, paper_only=False))
    d["pass_score"] = PASS_SCORE
    return d


def _status_from_fidelity(p: Persona) -> None:
    score = (p.fidelity or {}).get("score")
    if score is not None and score >= PASS_SCORE:
        if p.status == "draft":
            p.status, p.approved_at = "paper_only", datetime.now(UTC)
    elif p.status != "active":
        p.status, p.approved_at = "draft", None


@router.get("/personas")
def list_personas(s: Session = Depends(get_session)):
    out = []
    for p in s.exec(select(Persona)).all():
        maybe_promote(p, s)
        out.append(persona_out(p, s))
    return out


@router.get("/personas/{pid}")
def get_persona(pid: int, s: Session = Depends(get_session)):
    return persona_out(s.get(Persona, pid) or _404(), s, full=True)


class UploadIn(BaseModel):
    filename: str
    content_base64: str
    fidelity_filename: str | None = None  # 單獨上傳 SKILL.md 時，可另外附 FIDELITY.md
    fidelity_base64: str | None = None


@router.post("/personas/upload")
def upload_persona(body: UploadIn, s: Session = Depends(get_session)):
    """上傳女媧蒸餾好的人物：整個資料夾壓成 zip（含 SKILL.md、FIDELITY.md），或單獨的 SKILL.md（＋FIDELITY.md）"""
    try:
        data = base64.b64decode(body.content_base64)
        fid = base64.b64decode(body.fidelity_base64).decode("utf-8", errors="replace") if body.fidelity_base64 else None
    except ValueError as e:
        raise HTTPException(400, "檔案內容格式錯誤") from e
    if len(data) > 15_000_000:
        raise HTTPException(400, "檔案太大（上限 15 MB）")
    try:
        parsed = parse_nuwa(body.filename, data, fid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # 壞掉的 zip 等
        raise HTTPException(400, f"無法讀取檔案：{type(e).__name__}") from e
    p = Persona(name=parsed.name, summary=parsed.description[:300], profile=parsed.profile, source="nuwa",
                fidelity=parsed.fidelity.model_dump() if parsed.fidelity else {},
                meta={"files": parsed.files, "kept_sections": parsed.kept_sections,
                      "dropped_sections": parsed.dropped_sections, "truncated": parsed.truncated,
                      "filename": body.filename, "raw_skill": parsed.raw_skill,
                      "quality": parsed.quality.model_dump() if parsed.quality else None})
    _status_from_fidelity(p)
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s, full=True)


class PersonaUpdate(BaseModel):
    name: str
    summary: str = ""


@router.put("/personas/{pid}")
def update_persona(pid: int, body: PersonaUpdate, s: Session = Depends(get_session)):
    """只能改名稱與簡介；思維內容請在女媧重新蒸餾後重新上傳"""
    p = s.get(Persona, pid) or _404()
    p.name, p.summary = body.name, body.summary
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s, full=True)


@router.post("/personas/{pid}/promote")
def promote_persona(pid: int, s: Session = Depends(get_session)):
    """手動開放實盤（跳過模擬期剩餘時間）"""
    p = s.get(Persona, pid) or _404()
    if p.status != "paper_only":
        raise HTTPException(400, "只有保真度達標、模擬期中的大師可以開放實盤")
    p.status = "active"
    s.add(p)
    s.commit()
    s.refresh(p)
    return persona_out(p, s)


@router.delete("/personas/{pid}")
def delete_persona(pid: int, s: Session = Depends(get_session)):
    p = s.get(Persona, pid) or _404()
    if _bots_using(p, s, paper_only=False):
        raise HTTPException(400, "仍有組合（Bot）使用這位大師，請先刪除組合")
    s.delete(p)
    s.commit()
    return {"ok": True}


# ------------------------------------------------------------------ 大師組合
class PlanIn(BaseModel):
    ai_model_id: int


@router.post("/personas/{pid}/portfolio-plan")
async def portfolio_plan(pid: int, body: PlanIn, s: Session = Depends(get_session)):
    """AI 依大師的思維檔案設計他的量化組合（一次 AI 呼叫）"""
    p = s.get(Persona, pid) or _404()
    m = s.get(AIModelConfig, body.ai_model_id)
    if not m:
        raise HTTPException(404, "AI 模型不存在")
    try:
        plan = await design_plan(provider_from_config(m), p.name, p.profile)
    except AIProviderError as e:
        raise HTTPException(502, str(e)) from e
    p.meta = {**(p.meta or {}), "portfolio_plan": plan.model_dump()}
    s.add(p)
    s.commit()
    return plan.model_dump()


class PortfolioIn(BaseModel):
    ai_model_id: int
    account_id: int
    plan: PortfolioPlan
    capital: float | None = 10_000  # 模擬帳戶：這個組合自己的模擬資金
    name: str | None = None
    start: bool = True


@router.post("/personas/{pid}/portfolio")
async def create_portfolio(pid: int, body: PortfolioIn, s: Session = Depends(get_session)):
    """依計畫建立大師組合（AI 交易員 Bot，大腦＝這位大師）"""
    p = s.get(Persona, pid) or _404()
    acc = s.get(ExchangeAccount, body.account_id)
    if not acc:
        raise HTTPException(404, "交易所帳戶不存在")
    if not s.get(AIModelConfig, body.ai_model_id):
        raise HTTPException(404, "AI 模型不存在")
    f = plan_to_bot_fields(body.plan)
    from goldhunter.strategies.ai_strategy import AIStrategy

    st = StrategyConfig(name=f"AI 交易員｜{body.name or p.name + ' 組合'}", kind="ai", status="active", params={
        **AIStrategy.default_params, "instructions": f["instructions"] or AIStrategy.default_params["instructions"],
        "persona_id": p.id})
    s.add(st)
    s.commit()
    s.refresh(st)
    bot = Bot(name=body.name or f"{p.name} 組合", account_id=acc.id, strategy_id=st.id, ai_model_id=body.ai_model_id,
              symbols=f["symbols"], timeframe=f["timeframe"], interval_sec=f["interval_sec"],
              risk=RiskConfig(**f["risk"]).model_dump(), copilot={"review": False, "manage": False, "tune": False},
              universe=f["universe"], entry=f["entry"], capital=body.capital if acc.paper else None)
    s.add(bot)
    s.commit()
    s.refresh(bot)
    started, error = False, None
    if body.start:
        try:
            await manager.start(bot.id)  # type: ignore[arg-type]
            started = True
        except ValueError as e:
            error = str(e)
    return {"bot_id": bot.id, "started": started, "error": error}


@router.get("/masters")
def masters_overview(s: Session = Depends(get_session)):
    """大師組合總覽：每位大師的組合績效（權益曲線、報酬、最大回撤、勝率…）"""
    out = []
    for bot, st in s.exec(select(Bot, StrategyConfig).where(Bot.strategy_id == StrategyConfig.id,
                                                              StrategyConfig.kind == "ai")).all():
        pid = (st.params or {}).get("persona_id")
        if not pid:
            continue
        p = s.get(Persona, int(pid))
        snaps = s.exec(select(EquitySnapshot).where(EquitySnapshot.bot_id == bot.id)
                       .order_by(col(EquitySnapshot.ts))).all()
        trades = s.exec(select(Trade).where(Trade.bot_id == bot.id)).all()
        closed = [t.realized_pnl for t in trades if t.realized_pnl is not None]
        eq = [x.equity for x in snaps]
        start = eq[0] if eq else (bot.capital or None)
        peak, mdd = (eq[0] if eq else 0), 0.0
        for e in eq:
            peak = max(peak, e)
            mdd = max(mdd, (peak - e) / peak if peak else 0)
        step = max(1, len(snaps) // 200)
        days = ((snaps[-1].ts - snaps[0].ts).total_seconds() / 86400) if len(snaps) > 1 else 0.0
        acc = s.get(ExchangeAccount, bot.account_id)
        sample_warning = None
        if len(closed) < MIN_SAMPLE_TRADES or days < MIN_SAMPLE_DAYS:
            sample_warning = (f"樣本太少（{days:.1f} 天、平倉 {len(closed)} 筆），績效還不具參考性；"
                              f"至少跑滿 {MIN_SAMPLE_DAYS} 天、{MIN_SAMPLE_TRADES} 筆再比較")
        runner = manager.runners.get(bot.id)  # type: ignore[arg-type]
        out.append({
            "bot_id": bot.id, "name": bot.name, "running": bool(runner and runner.running), "status": bot.status,
            "persona": {"id": p.id, "name": p.name, "status": p.status,
                        "fidelity": (p.fidelity or {}).get("score")} if p else None,
            "timeframe": bot.timeframe, "universe": bot.universe, "symbols": bot.symbols,
            "risk": {k: (bot.risk or {}).get(k) for k in ("max_leverage", "max_positions", "max_position_pct",
                                                           "long_only")},
            "plan": ((p.meta or {}).get("portfolio_plan") if p else None),
            "start_equity": start, "equity": eq[-1] if eq else None,
            "return_pct": round((eq[-1] / start - 1) * 100, 2) if eq and start else None,
            "max_drawdown_pct": round(mdd * 100, 2),
            "trades": len(trades), "closed_trades": len(closed),
            "win_rate_pct": round(sum(1 for x in closed if x > 0) / len(closed) * 100, 1) if closed else None,
            "realized_pnl": round(sum(closed), 2),
            "open_positions": len(runner.exchange.positions) if runner and hasattr(runner.exchange, "positions") else None,
            "days": round(days, 1), "paper": bool(acc.paper) if acc else None, "sample_warning": sample_warning,
            "curve": [{"ts": x.ts, "pct": round((x.equity / start - 1) * 100, 3) if start else 0}
                      for x in snaps[::step]],
        })
    return out


def _404(msg: str = "找不到這位大師"):
    raise HTTPException(404, msg)
