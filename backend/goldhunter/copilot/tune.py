"""C. AI 參數微調（含防過度擬合）。

1. 取最近 N 根 K 線，前 70% 為「樣本內」、後 30% 為「樣本外」
2. AI 只看到樣本內的回測結果與行情概況，在你允許的範圍內提出新參數
3. 新舊參數都在「樣本外」回測；新參數報酬較好且回撤沒有明顯變差，才算通過
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from goldhunter.ai.base import AIProvider
from goldhunter.backtest.engine import BacktestConfig, run_backtest
from goldhunter.copilot.config import CopilotConfig
from goldhunter.core.models import Candle, Instrument
from goldhunter.risk.manager import RiskConfig
from goldhunter.strategies.base import Strategy

TUNE_SYSTEM = """你是量化策略研究員。根據策略在近期行情的回測表現與盤勢特徵，
在允許範圍內提出一組新參數，目標是提高風險調整後報酬（兼顧報酬與回撤），避免過度擬合。
只能調整 ranges 內列出的參數，且必須落在範圍內。整數參數請給整數。
reasoning 用繁體中文，150 字內，說明盤勢判斷與調整理由。只輸出符合 schema 的 JSON。"""


class TuneOutcome(BaseModel):
    current_params: dict[str, Any]
    proposed_params: dict[str, Any]
    current_metrics: dict
    proposed_metrics: dict
    reasoning: str
    passed: bool
    note: str = ""


def clamp_params(proposed: dict, current: dict, ranges: dict[str, list[float]]) -> dict:
    out = dict(current)
    for k, (lo, hi) in ranges.items():
        if k not in proposed or k not in current:
            continue
        try:
            v = float(proposed[k])
        except (TypeError, ValueError):
            continue
        v = min(max(v, lo), hi)
        out[k] = int(round(v)) if isinstance(current[k], int) and not isinstance(current[k], bool) else v
    return out


def _regime(candles: list[Candle]) -> str:
    closes = [c.close for c in candles]
    change = (closes[-1] / closes[0] - 1) * 100
    rets = [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
    vol = (sum(r * r for r in rets) / max(len(rets), 1)) ** 0.5 * 100
    return f"區間漲跌 {change:.2f}%，單根 K 線波動率約 {vol:.3f}%，共 {len(candles)} 根"


def better(cur: dict, new: dict) -> bool:
    if not new or not cur:
        return False
    ret_ok = new.get("total_return_pct", -1e9) > cur.get("total_return_pct", -1e9)
    dd_ok = new.get("max_drawdown_pct", 1e9) <= max(cur.get("max_drawdown_pct", 0) * 1.2, cur.get("max_drawdown_pct", 0) + 1)
    return ret_ok and dd_ok


async def tune_strategy(ai: AIProvider, strategy_cls: type[Strategy], current: dict, inst: Instrument, timeframe: str,
                        candles: list[Candle], cfg: CopilotConfig, risk: RiskConfig) -> TuneOutcome:
    if not cfg.tune_ranges:
        raise ValueError("尚未設定可調整的參數範圍（tune_ranges）")
    if strategy_cls.uses_ai:
        raise ValueError("AI 型策略不適用參數微調")
    split = int(len(candles) * 0.7)
    ins, oos = candles[:split], candles[split - strategy_cls.warmup:]
    bt_cfg = BacktestConfig(risk=risk.model_copy(update={"daily_loss_limit_pct": 0, "max_orders_per_hour": 10_000}))

    ins_res = await run_backtest(strategy_cls(current), inst, timeframe, ins, bt_cfg)
    prompt = json.dumps({
        "strategy": strategy_cls.name, "description": strategy_cls.description,
        "current_params": current, "ranges": cfg.tune_ranges,
        "in_sample_metrics": ins_res.metrics, "market_regime": _regime(ins),
        "recent_regime": _regime(ins[-200:]), "user_notes": cfg.notes,
    }, ensure_ascii=False)
    schema = {
        "type": "object",
        "properties": {
            "params": {"type": "object", "properties": {k: {"type": "number"} for k in cfg.tune_ranges},
                       "required": list(cfg.tune_ranges), "additionalProperties": False},
            "reasoning": {"type": "string"},
        },
        "required": ["params", "reasoning"],
        "additionalProperties": False,
    }
    result = await ai.complete_json(TUNE_SYSTEM, prompt, schema)
    proposed = clamp_params(result.decision.get("params", {}), current, cfg.tune_ranges)

    cur_oos = await run_backtest(strategy_cls(current), inst, timeframe, oos, bt_cfg)
    new_oos = await run_backtest(strategy_cls(proposed), inst, timeframe, oos, bt_cfg)
    passed = proposed != current and better(cur_oos.metrics, new_oos.metrics)
    note = "樣本外表現較佳" if passed else ("參數未變" if proposed == current else "樣本外表現未優於現行參數")
    return TuneOutcome(current_params=current, proposed_params=proposed, current_metrics=cur_oos.metrics,
                       proposed_metrics=new_oos.metrics, reasoning=result.decision.get("reasoning", ""),
                       passed=passed, note=note)
