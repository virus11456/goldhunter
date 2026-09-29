from __future__ import annotations

from sqlmodel import Session

from goldhunter.personas.lifecycle import check_persona_usable
from goldhunter.store.db import Persona
from goldhunter.strategies.ai_strategy import AIStrategy
from goldhunter.strategies.base import Strategy


def attach_personas(strategy: Strategy, s: Session, paper_account: bool = True, check: bool = True) -> None:
    """AI 交易員：依 persona_id 載入大師的交易思維檔案（實盤需保真度達標且走完模擬期）"""
    if not isinstance(strategy, AIStrategy) or not (pid := strategy.params.get("persona_id")):
        return
    p = s.get(Persona, int(pid))
    if not p:
        raise ValueError(f"投資大師 #{pid} 不存在")
    if check:
        check_persona_usable(p, paper_account, s)
    strategy.persona = (p.name, p.profile)
