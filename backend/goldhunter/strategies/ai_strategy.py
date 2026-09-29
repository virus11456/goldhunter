"""AI 型策略：把行情、指標、持倉整理成 prompt，交給 AI 模型輸出決策。"""

from __future__ import annotations

import json

from goldhunter.ai.base import DECISION_SCHEMA
from goldhunter.core.models import Action, Decision
from goldhunter.strategies import ta
from goldhunter.strategies.base import Strategy, StrategyContext


def build_market_prompt(ctx: StrategyContext, instructions: str, bars: int = 30, extra: str = "") -> str:
    close = ctx.close
    ind = {
        "ema20": ta.ema(close, 20)[-1],
        "ema50": ta.ema(close, 50)[-1],
        "rsi14": ta.rsi(close, 14)[-1],
        "atr14": ta.atr(ctx.high, ctx.low, close, 14)[-1],
    }
    macd_line, macd_sig, _ = ta.macd(close)
    ind["macd"], ind["macd_signal"] = macd_line[-1], macd_sig[-1]
    recent = [[c.ts, c.open, c.high, c.low, c.close, round(c.volume, 4)] for c in ctx.candles[-bars:]]
    pos = ctx.position
    return f"""## 商品
{ctx.instrument}（K 線週期 {ctx.timeframe}），最新價 {ctx.price}
可放空：{ctx.capabilities.supports_short}；槓桿上限：{ctx.max_leverage}；單筆倉位上限：{ctx.max_size_pct}% 權益

## 技術指標（最新值）
{json.dumps({k: (round(v, 6) if v is not None else None) for k, v in ind.items()}, ensure_ascii=False)}

## 最近 {len(recent)} 根 K 線 [ts, open, high, low, close, volume]
{json.dumps(recent)}

## 帳戶
權益 {ctx.balance.total:.2f} {ctx.balance.currency}，可用 {ctx.balance.free:.2f}
目前持倉：{"無" if not pos else f"{pos.side} 數量 {abs(pos.quantity)} 均價 {pos.entry_price} 未實現損益 {pos.unrealized_pnl:.2f}"}
{extra}
## 策略指示
{instructions or "趨勢跟隨為主，嚴格控制風險。"}
"""


TRADER_SYSTEM = """你是一位紀律嚴謹的加密貨幣永續合約交易員（AI 交易員），自主管理這個帳戶。
根據使用者提供的行情、技術指標、市場情報（新聞、總經、合約數據、情緒）、目前持倉、近期績效與交易偏好，
對這個交易對輸出單一決策：
- open_long / open_short：開倉（已有反向持倉時代表反手）
- close：平掉目前持倉
- hold：不動作（沒有明確優勢時就選 hold，寧可錯過不要做錯）
規則：
- 只能輸出符合 schema 的 JSON。
- 開倉必須給 stop_loss；建議給 take_profit，盈虧比至少 1:1.5。
- size_pct（佔權益 %）與 leverage 不得超過使用者給的上限。
- 重大經濟數據公布前、資金費率極端或新聞重大利空時，要特別保守。
- 若有「參考策略訊號」，把它當成一個參考意見，不必盲從。
- reasoning 用繁體中文，150 字內，列出關鍵依據（技術面 / 籌碼面 / 消息面）。"""


REVIEW_VOTE_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["approve", "veto"]}, "reasoning": {"type": "string"}},
    "required": ["verdict", "reasoning"],
    "additionalProperties": False,
}


class AIStrategy(Strategy):
    """AI 交易員：不需要事先寫策略，AI 自主判斷多空、倉位與止損。"""

    name = "ai"
    description = "AI 交易員：AI 綜合行情、新聞、總經與合約數據，自主決定多空、倉位與止損（可參考你的策略訊號）"
    default_params = {
        "instructions": "順勢交易為主，嚴格止損，盈虧比至少 1:2；重大數據公布前不追價。",
        "bars": 40,
        "min_confidence": 0.6,
        "reference_strategy_id": None,  # 選填：讓 AI 參考的策略
        "persona_id": None,  # 選填：交易大腦（投資大師）
        "reviewer_ids": [],  # 選填：審查委員（投資大師），可否決開倉
        "veto_rule": "any",  # any＝任一委員否決就不做；majority＝否決票過半才不做
    }
    warmup = 60
    uses_ai = True

    def __init__(self, params=None, ai=None):
        super().__init__(params, ai)
        self.reference: Strategy | None = None  # 由 BotManager 依 reference_strategy_id 注入
        self.reference_name: str | None = None
        self.persona: tuple[str, str] | None = None  # (名字, 思維檔案)，由 attach_personas 注入
        self.reviewers: list[tuple[str, str]] = []
        self.last_ai_result = None

    def _system(self) -> str:
        if not self.persona:
            return TRADER_SYSTEM
        name, profile = self.persona
        return (f"{TRADER_SYSTEM}\n\n## 你的交易大腦：{name}\n以下是{name}的交易思維檔案。用他的心智模型、決策規則與反模式來判斷；"
                f"檔案說他不適合的情境（例如不交易某類商品）就選 hold。reasoning 開頭寫「以{name}的角度」。\n\n{profile}")

    async def _committee(self, decision: Decision, prompt: str) -> tuple[bool, list[dict]]:
        """審查委員逐一表決；回傳（是否否決, 各委員意見）"""
        opinions = []
        desc = (f"## 待審交易\n{decision.action.value}，倉位 {decision.size_pct}%、槓桿 {decision.leverage}x、"
                f"止損 {decision.stop_loss}、止盈 {decision.take_profit}\n交易員理由：{decision.reasoning}")
        for name, profile in self.reviewers:
            system = (f"你是投資委員會的審查委員：{name}。依下列思維檔案審查這筆交易，只能 approve（放行）或 veto（否決）。"
                      f"reasoning 用繁體中文 80 字內、用{name}的語氣。\n\n{profile}")
            try:
                r = await self.ai.complete_json(system, prompt + "\n\n" + desc, REVIEW_VOTE_SCHEMA)
                v = r.decision
                opinions.append({"name": name, "verdict": "veto" if v.get("verdict") == "veto" else "approve",
                                 "reasoning": v.get("reasoning", "")})
            except Exception as e:  # 審查失敗時保守視為否決
                opinions.append({"name": name, "verdict": "veto", "reasoning": f"審查失敗，保守否決：{e}"})
        vetoes = sum(1 for o in opinions if o["verdict"] == "veto")
        if self.p("veto_rule") == "majority":
            vetoed = vetoes * 2 > len(opinions)
        else:
            vetoed = vetoes > 0
        return vetoed, opinions

    async def _reference_text(self, ctx: StrategyContext) -> str:
        if not self.reference:
            return ""
        try:
            sig = await self.reference.run(ctx)
        except Exception as e:  # 參考策略出錯不影響 AI 判斷
            return f"\n## 參考策略訊號\n參考策略「{self.reference_name}」執行失敗：{e}"
        if sig is None:
            desc = "目前沒有訊號"
        else:
            desc = f"{sig.action.value}（倉位 {sig.size_pct}%、止損 {sig.stop_loss}）理由：{sig.reasoning}"
        return f"\n## 參考策略訊號\n你的策略「{self.reference_name}」：{desc}"

    async def on_bar(self, ctx: StrategyContext):
        if self.ai is None:
            raise RuntimeError("AI 交易員需要設定 AI 模型")
        extra = ""
        if ctx.recent_pnls:
            wins = sum(1 for p in ctx.recent_pnls if p > 0)
            extra += (f"\n近期已平倉 {len(ctx.recent_pnls)} 筆：勝 {wins} 敗 {len(ctx.recent_pnls) - wins}，"
                      f"合計 {sum(ctx.recent_pnls):.2f}")
        extra += await self._reference_text(ctx)
        prompt = build_market_prompt(ctx, self.p("instructions"), int(self.p("bars")), extra=extra)
        if ctx.intel:
            prompt += "\n\n" + ctx.intel
        result = await self.ai.complete_json(self._system(), prompt, DECISION_SCHEMA)
        d = result.decision
        action = d.get("action", "hold")
        decision = Decision(
            instrument=ctx.instrument,
            action=Action(action) if action in Action._value2member_map_ else Action.HOLD,
            size_pct=max(0.0, min(float(d.get("size_pct") or 0), 100.0)),
            leverage=max(1, int(d.get("leverage") or 1)),
            stop_loss=d.get("stop_loss"),
            take_profit=d.get("take_profit"),
            confidence=max(0.0, min(float(d.get("confidence") or 0), 1.0)),
            reasoning=d.get("reasoning", ""),
            source="ai",
        )
        self.last_ai_result = result  # 供引擎寫入決策紀錄
        meta: dict = {}
        if self.persona:
            meta["persona"] = self.persona[0]
        if decision.action != Action.HOLD and decision.confidence < self.p("min_confidence"):
            decision.reasoning = f"[信心 {decision.confidence:.2f} 低於門檻，改為觀望] {decision.reasoning}"
            decision.action = Action.HOLD
        if decision.action in (Action.OPEN_LONG, Action.OPEN_SHORT) and self.reviewers:
            vetoed, opinions = await self._committee(decision, prompt)
            meta["committee"] = {"rule": self.p("veto_rule"), "vetoed": vetoed, "opinions": opinions,
                                 "proposed": decision.action.value}
            if vetoed:
                who = "、".join(o["name"] for o in opinions if o["verdict"] == "veto")
                decision.reasoning = f"[委員會否決：{who}] {decision.reasoning}"
                decision.action = Action.HOLD
        decision.meta = meta or None
        return decision
