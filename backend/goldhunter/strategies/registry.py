from __future__ import annotations

from typing import Any

from goldhunter.strategies.ai_strategy import AIStrategy
from goldhunter.strategies.base import Strategy
from goldhunter.strategies.builtin import MACrossStrategy, RSIReversionStrategy
from goldhunter.strategies.custom import load_strategy_class
from goldhunter.strategies.mrspencer import MrSpencerStrategy

BUILTIN: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (MACrossStrategy, RSIReversionStrategy, MrSpencerStrategy, AIStrategy)}


def recommended_risk(kind: str | None, code: str | None = None) -> dict[str, Any]:
    """策略宣告的建議風控；讀不到就回傳空 dict"""
    try:
        if kind == "python" and code:
            return dict(load_strategy_class(code).recommended_risk or {})
        return dict(BUILTIN[kind].recommended_risk or {}) if kind in BUILTIN else {}
    except Exception:
        return {}


def list_strategy_types() -> list[dict]:
    items = [
        {"type": k, "description": v.description, "default_params": v.default_params, "uses_ai": v.uses_ai,
         "recommended_risk": v.recommended_risk}
        for k, v in BUILTIN.items()
    ]
    items.sort(key=lambda x: x["type"] != "ai")  # AI 交易員排第一
    items.append({"type": "python", "description": "自訂 Python 策略（可由 TradingView Pine Script 轉換）",
                  "default_params": {}, "uses_ai": False})
    items.append({"type": "tradingview", "description": "TradingView 訊號：由 TradingView Alert Webhook 觸發下單",
                  "default_params": {}, "uses_ai": False})
    return items


def attach_reference(strategy: Strategy, session, paper_account: bool = True, check: bool = True) -> None:
    """AI 交易員：依 reference_strategy_id 載入要參考的策略（只接受規則型 / 自訂 Python、且已啟用）"""
    ref_id = strategy.params.get("reference_strategy_id") if isinstance(strategy, AIStrategy) else None
    if not ref_id:
        return
    from goldhunter.store.db import StrategyConfig
    from goldhunter.strategies.lifecycle import check_usable

    cfg = session.get(StrategyConfig, int(ref_id))
    if not cfg or cfg.kind in ("ai", "tradingview"):
        raise ValueError("參考策略不存在，或類型不支援（只能參考規則型或自訂 Python 策略）")
    if check:  # 回測不需檢查上線狀態
        check_usable(cfg, paper_account, session)
    strategy.reference = build_strategy(cfg.kind, cfg.params, cfg.code)  # type: ignore[attr-defined]
    strategy.reference_name = cfg.name  # type: ignore[attr-defined]


def build_strategy(kind: str, params: dict[str, Any] | None = None, code: str | None = None, ai=None) -> Strategy:
    if kind == "python":
        if not code:
            raise ValueError("python 策略需要程式碼")
        return load_strategy_class(code)(params, ai=ai)
    if kind not in BUILTIN:
        raise ValueError(f"未知的策略類型：{kind}")
    return BUILTIN[kind](params, ai=ai)
