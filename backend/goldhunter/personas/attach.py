from __future__ import annotations

from sqlmodel import Session

from goldhunter.personas.lifecycle import check_persona_usable
from goldhunter.store.db import Persona
from goldhunter.strategies.ai_strategy import AIStrategy
from goldhunter.strategies.base import Strategy


def attach_personas(strategy: Strategy, s: Session, paper_account: bool = True, check: bool = True) -> None:
    """AI 交易員：依 persona_id / reviewer_ids 載入大師思維檔案（實盤需通過保真度與模擬期）"""
    if not isinstance(strategy, AIStrategy):
        return

    def load(pid) -> Persona:
        p = s.get(Persona, int(pid))
        if not p:
            raise ValueError(f"投資大師 #{pid} 不存在")
        if check:
            check_persona_usable(p, paper_account, s)
        return p

    if pid := strategy.params.get("persona_id"):
        p = load(pid)
        strategy.persona = (p.name, p.profile)
    strategy.reviewers = [(p.name, p.profile) for p in (load(r) for r in strategy.params.get("reviewer_ids") or [])]
