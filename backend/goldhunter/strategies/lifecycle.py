"""策略上線流程：待審核 → 模擬期（只能用模擬帳戶）→ 開放實盤。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, col, func, select

from goldhunter.store.db import Bot, ExchangeAccount, StrategyConfig, Trade

STATUS_LABEL = {"pending_review": "待審核", "paper_only": "模擬期", "active": "已啟用"}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def promotion_progress(st: StrategyConfig, s: Session) -> dict | None:
    """模擬期進度：已過幾天、模擬帳戶已平倉幾筆"""
    if st.status != "paper_only" or not st.approved_at:
        return None
    since = _aware(st.approved_at)
    days = (datetime.now(UTC) - since).total_seconds() / 86400
    bot_ids = [b.id for b, acc in s.exec(
        select(Bot, ExchangeAccount).where(Bot.strategy_id == st.id, Bot.account_id == ExchangeAccount.id,
                                           ExchangeAccount.paper == True)  # noqa: E712
    )]
    trades = 0
    if bot_ids:
        trades = s.exec(select(func.count()).select_from(Trade).where(
            col(Trade.bot_id).in_(bot_ids), col(Trade.realized_pnl).is_not(None), Trade.ts >= since)).one()
    return {
        "days": round(days, 1), "days_required": st.paper_days,
        "trades": int(trades), "trades_required": st.min_paper_trades,
        "ready": days >= st.paper_days and trades >= st.min_paper_trades,
    }


def maybe_promote(st: StrategyConfig, s: Session) -> bool:
    prog = promotion_progress(st, s)
    if prog and prog["ready"]:
        st.status = "active"
        s.add(st)
        s.commit()
        s.refresh(st)
        return True
    return False


def check_usable(st: StrategyConfig, paper_account: bool, s: Session | None = None) -> None:
    """Bot 啟動前檢查策略能不能用在這個帳戶"""
    if s is not None:
        maybe_promote(st, s)
    if st.status == "active":
        return
    if st.status == "paper_only":
        if paper_account:
            return
        prog = promotion_progress(st, s) if s is not None else None
        left = f"（還需 {max(0, st.paper_days - prog['days']):.1f} 天、{max(0, st.min_paper_trades - prog['trades'])} 筆模擬成交）" if prog else ""
        raise ValueError(f"策略「{st.name}」仍在模擬期，只能用在模擬帳戶{left}；可在策略頁手動開放實盤")
    raise ValueError(f"策略「{st.name}」尚未通過審核，不能交易")
