"""大師上線流程：草稿（只能模擬）→ 保真度通過進入模擬期 → 滿 N 天且模擬成交達 M 筆後開放實盤。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, col, func, select

from goldhunter.personas.distill import BUILTIN, builtin_profile
from goldhunter.store.db import Bot, ExchangeAccount, Persona, StrategyConfig, Trade

STATUS_LABEL = {"draft": "未評分", "paper_only": "模擬期", "active": "已啟用"}


def seed_builtin(s: Session) -> None:
    """確保內建大師存在（只新增，不覆蓋使用者的修改）"""
    existing = {p.slug for p in s.exec(select(Persona).where(col(Persona.slug).is_not(None)))}
    for slug, info in BUILTIN.items():
        if slug in existing:
            continue
        s.add(Persona(slug=slug, name=info["name"], role=info["role"], markets=info["markets"],
                      summary=info["summary"], profile=builtin_profile(slug), source="builtin"))
    s.commit()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _bots_using(p: Persona, s: Session, paper_only: bool = True) -> list[int]:
    ids = []
    for bot, st, acc in s.exec(select(Bot, StrategyConfig, ExchangeAccount).where(
            Bot.strategy_id == StrategyConfig.id, Bot.account_id == ExchangeAccount.id)):
        params = st.params or {}
        uses = st.kind == "ai" and (params.get("persona_id") == p.id or p.id in (params.get("reviewer_ids") or []))
        if uses and (acc.paper or not paper_only):
            ids.append(bot.id)
    return ids


def persona_progress(p: Persona, s: Session) -> dict | None:
    if p.status != "paper_only" or not p.approved_at:
        return None
    since = _aware(p.approved_at)
    days = (datetime.now(UTC) - since).total_seconds() / 86400
    bot_ids = _bots_using(p, s)
    trades = 0
    if bot_ids:
        trades = s.exec(select(func.count()).select_from(Trade).where(
            col(Trade.bot_id).in_(bot_ids), col(Trade.realized_pnl).is_not(None), Trade.ts >= since)).one()
    return {"days": round(days, 1), "days_required": p.paper_days, "trades": int(trades),
            "trades_required": p.min_paper_trades,
            "ready": days >= p.paper_days and trades >= p.min_paper_trades}


def maybe_promote(p: Persona, s: Session) -> bool:
    prog = persona_progress(p, s)
    if prog and prog["ready"]:
        p.status = "active"
        s.add(p)
        s.commit()
        s.refresh(p)
        return True
    return False


def check_persona_usable(p: Persona, paper_account: bool, s: Session) -> None:
    maybe_promote(p, s)
    if p.status == "active" or paper_account:
        return
    if p.status == "paper_only":
        prog = persona_progress(p, s) or {}
        raise ValueError(f"大師「{p.name}」仍在模擬期，只能用在模擬帳戶"
                         f"（模擬 {prog.get('days', 0)}/{p.paper_days} 天、成交 {prog.get('trades', 0)}/{p.min_paper_trades} 筆）")
    raise ValueError(f"大師「{p.name}」尚未通過保真度評分，只能用在模擬帳戶")
