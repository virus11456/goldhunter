"""A. 訊號審核 + B. 持倉管理。

AI 的輸出一律經過程式硬性約束（constrain_*），就算模型亂回也不可能：
- 改變交易方向
- 放大超過設定的倉位倍數
- 把止損放寬（只能收緊）
- 在持倉管理中加倉或開新倉
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from goldhunter.ai.base import AIProvider, AIResult
from goldhunter.copilot.config import CopilotConfig
from goldhunter.core.models import Action, Decision, Position
from goldhunter.intel.hub import IntelSnapshot
from goldhunter.strategies.ai_strategy import build_market_prompt
from goldhunter.strategies.base import StrategyContext

REVIEW_SYSTEM = """你是量化交易團隊的風險分析師（AI 副駕駛）。使用者的交易策略剛產生一個進場訊號，
你要結合技術面、合約數據、市場情緒、新聞與總經事件，判斷是否該執行，並可微調倉位與止損止盈。
原則：
- 你不能改變方向，只能 approve（放行）、adjust（放行但調整）、veto（否決）。
- 有明確理由才否決（例如重大利空新聞、重要數據即將公布、資金費率極端擁擠、策略近期連續虧損且盤勢不利）。
- size_multiplier：1 為原倉位；看好可放大、疑慮可縮小。
- stop_loss / take_profit：可給新價格或 null（沿用原本）。止損只能比原本更靠近現價（收緊）。
- reasoning 用繁體中文，150 字內，列出關鍵依據。只輸出符合 schema 的 JSON。"""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "adjust", "veto"]},
        "size_multiplier": {"type": "number"},
        "stop_loss": {"type": ["number", "null"]},
        "take_profit": {"type": ["number", "null"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "size_multiplier", "stop_loss", "take_profit", "confidence", "reasoning"],
    "additionalProperties": False,
}

MANAGE_SYSTEM = """你是量化交易團隊的持倉管理員（AI 副駕駛）。使用者目前持有一個部位，
請結合最新行情、合約數據、情緒、新聞與總經事件，判斷是否需要降低風險。
你只能選擇：
- hold：維持不動
- close：全部平倉
- reduce：減倉 reduce_pct（1-99）%
- move_stop：把止損移到 new_stop（只能往有利方向移動，例如多單只能往上移，常用於移到成本價保本）
不能加倉、不能開新倉。沒有明確理由時選 hold。
reasoning 用繁體中文，150 字內。只輸出符合 schema 的 JSON。"""

MANAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "close", "reduce", "move_stop"]},
        "reduce_pct": {"type": ["number", "null"]},
        "new_stop": {"type": ["number", "null"]},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "reduce_pct", "new_stop", "confidence", "reasoning"],
    "additionalProperties": False,
}


class ReviewOutcome(BaseModel):
    verdict: str  # approve / adjust / veto
    decision: Decision | None  # 調整後的決策；veto 時為 None
    reasoning: str
    notes: list[str] = []  # 程式約束修正紀錄
    ai: AIResult | None = None


class ManageOutcome(BaseModel):
    action: str  # hold / close / reduce / move_stop
    reduce_pct: float | None = None
    new_stop: float | None = None
    reasoning: str = ""
    notes: list[str] = []
    ai: AIResult | None = None


def _perf_text(recent_pnls: list[float]) -> str:
    if not recent_pnls:
        return "此策略近期尚無已平倉交易。"
    wins = sum(1 for p in recent_pnls if p > 0)
    return (f"此策略最近 {len(recent_pnls)} 筆已平倉交易：勝 {wins} 敗 {len(recent_pnls) - wins}，"
            f"合計損益 {sum(recent_pnls):.2f}，由舊到新：{[round(p, 2) for p in recent_pnls]}")


def constrain_review(raw: dict, signal: Decision, price: float, cfg: CopilotConfig) -> ReviewOutcome:
    notes: list[str] = []
    verdict = raw.get("verdict", "veto")
    conf = float(raw.get("confidence") or 0)
    reasoning = str(raw.get("reasoning", ""))
    if verdict not in ("approve", "adjust", "veto"):
        verdict, reasoning = "veto", f"AI 回傳無法辨識的結果，保守否決。{reasoning}"
    if verdict != "veto" and conf < cfg.review_min_confidence:
        notes.append(f"AI 信心 {conf:.2f} 低於門檻 {cfg.review_min_confidence}，改為否決")
        verdict = "veto"
    if verdict == "veto":
        return ReviewOutcome(verdict="veto", decision=None, reasoning=reasoning, notes=notes)

    d = signal.model_copy()
    long = d.action == Action.OPEN_LONG
    mult = float(raw.get("size_multiplier") or 1)
    clamped = min(max(mult, cfg.size_mult_min), cfg.size_mult_max)
    if clamped != mult:
        notes.append(f"倉位倍數 {mult}→{clamped}")
    d.size_pct = round(d.size_pct * clamped, 4)
    if d.quantity:
        d.quantity = d.quantity * clamped  # 固定數量的策略（分批加碼）一樣按倍數調整

    sl = raw.get("stop_loss")
    if sl is not None:
        sl = float(sl)
        valid_side = sl < price if long else sl > price
        tighter = d.stop_loss is None or (sl >= d.stop_loss if long else sl <= d.stop_loss)
        if valid_side and tighter:
            d.stop_loss = sl
        else:
            notes.append(f"AI 止損 {sl} 不符規則（方向錯誤或放寬），沿用原止損")
    tp = raw.get("take_profit")
    if tp is not None:
        tp = float(tp)
        if (tp > price) if long else (tp < price):
            d.take_profit = tp
        else:
            notes.append(f"AI 止盈 {tp} 方向錯誤，已忽略")
    d.confidence = max(0.0, min(conf, 1.0))
    d.reasoning = f"{signal.reasoning}｜AI：{reasoning}"
    d.source = f"{signal.source}+ai"
    changed = clamped != 1 or d.stop_loss != signal.stop_loss or d.take_profit != signal.take_profit
    return ReviewOutcome(verdict="adjust" if changed else "approve", decision=d, reasoning=reasoning, notes=notes)


def constrain_manage(raw: dict, pos: Position, current_stop: float | None, price: float) -> ManageOutcome:
    action = raw.get("action", "hold")
    notes: list[str] = []
    out = ManageOutcome(action="hold", reasoning=str(raw.get("reasoning", "")))
    long = pos.quantity > 0
    if action == "close":
        out.action = "close"
    elif action == "reduce":
        pct = raw.get("reduce_pct")
        if pct is not None and 0 < float(pct) < 100:
            out.action, out.reduce_pct = "reduce", float(pct)
        else:
            notes.append(f"減倉比例 {pct} 無效，維持不動")
    elif action == "move_stop":
        ns = raw.get("new_stop")
        if ns is not None:
            ns = float(ns)
            valid_side = ns < price if long else ns > price
            better = current_stop is None or (ns > current_stop if long else ns < current_stop)
            if valid_side and better:
                out.action, out.new_stop = "move_stop", ns
            else:
                notes.append(f"新止損 {ns} 不符規則（只能往有利方向移動且不能越過現價），維持不動")
    out.notes = notes
    return out


async def review_signal(ai: AIProvider, signal: Decision, ctx: StrategyContext, intel: IntelSnapshot,
                        cfg: CopilotConfig, recent_pnls: list[float]) -> ReviewOutcome:
    signal_desc = json.dumps({
        "action": signal.action.value, "size_pct": signal.size_pct, "leverage": signal.leverage,
        "stop_loss": signal.stop_loss, "take_profit": signal.take_profit, "strategy_reason": signal.reasoning,
    }, ensure_ascii=False)
    prompt = "\n\n".join([
        build_market_prompt(ctx, cfg.notes or "（使用者未提供額外說明）"),
        intel.to_prompt(),
        f"## 策略績效\n{_perf_text(recent_pnls)}",
        f"## 待審核訊號\n{signal_desc}\n倉位倍數允許範圍：{cfg.size_mult_min}～{cfg.size_mult_max}",
    ])
    result = await ai.complete_json(REVIEW_SYSTEM, prompt, REVIEW_SCHEMA)
    out = constrain_review(result.decision, signal, ctx.price, cfg)
    out.ai = result
    return out


async def manage_position(ai: AIProvider, pos: Position, current_stop: float | None, current_tp: float | None,
                          ctx: StrategyContext, intel: IntelSnapshot, cfg: CopilotConfig) -> ManageOutcome:
    pnl_pct = (ctx.price / pos.entry_price - 1) * 100 * (1 if pos.quantity > 0 else -1) if pos.entry_price else 0
    prompt = "\n\n".join([
        build_market_prompt(ctx, cfg.notes or "（使用者未提供額外說明）"),
        intel.to_prompt(),
        f"## 持倉狀況\n方向 {pos.side}，均價 {pos.entry_price}，現價 {ctx.price}，"
        f"價格變動 {pnl_pct:.2f}%，目前止損 {current_stop}，止盈 {current_tp}",
    ])
    result = await ai.complete_json(MANAGE_SYSTEM, prompt, MANAGE_SCHEMA)
    out = constrain_manage(result.decision, pos, current_stop, ctx.price)
    out.ai = result
    return out
