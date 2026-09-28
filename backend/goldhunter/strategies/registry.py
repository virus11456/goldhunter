from __future__ import annotations

from typing import Any

from goldhunter.strategies.ai_strategy import AIStrategy
from goldhunter.strategies.base import Strategy
from goldhunter.strategies.builtin import MACrossStrategy, RSIReversionStrategy
from goldhunter.strategies.custom import load_strategy_class

BUILTIN: dict[str, type[Strategy]] = {cls.name: cls for cls in (MACrossStrategy, RSIReversionStrategy, AIStrategy)}


def list_strategy_types() -> list[dict]:
    items = [
        {"type": k, "description": v.description, "default_params": v.default_params, "uses_ai": v.uses_ai}
        for k, v in BUILTIN.items()
    ]
    items.append({"type": "python", "description": "自訂 Python 策略（可由 TradingView Pine Script 轉換）",
                  "default_params": {}, "uses_ai": False})
    items.append({"type": "tradingview", "description": "TradingView 訊號：由 TradingView Alert Webhook 觸發下單",
                  "default_params": {}, "uses_ai": False})
    return items


def build_strategy(kind: str, params: dict[str, Any] | None = None, code: str | None = None, ai=None) -> Strategy:
    if kind == "python":
        if not code:
            raise ValueError("python 策略需要程式碼")
        return load_strategy_class(code)(params, ai=ai)
    if kind not in BUILTIN:
        raise ValueError(f"未知的策略類型：{kind}")
    return BUILTIN[kind](params, ai=ai)
