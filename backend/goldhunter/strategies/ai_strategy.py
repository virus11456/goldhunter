"""AI 型策略：把行情、指標、持倉整理成 prompt，交給 AI 模型輸出決策。"""

from __future__ import annotations

import json

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


class AIStrategy(Strategy):
    name = "ai"
    description = "AI 決策：由你選擇的 AI 模型根據行情與你的策略指示做判斷"
    default_params = {
        "instructions": "趨勢跟隨，順勢交易，嚴格止損，盈虧比至少 1:2。",
        "bars": 30,
        "min_confidence": 0.6,
    }
    warmup = 60
    uses_ai = True

    async def on_bar(self, ctx: StrategyContext):
        if self.ai is None:
            raise RuntimeError("AI 策略需要設定 AI 模型")
        prompt = build_market_prompt(ctx, self.p("instructions"), int(self.p("bars")))
        result = await self.ai.decide(prompt)
        d = result.decision
        decision = Decision(
            instrument=ctx.instrument,
            action=Action(d["action"]),
            size_pct=max(0.0, min(float(d.get("size_pct") or 0), 100.0)),
            leverage=max(1, int(d.get("leverage") or 1)),
            stop_loss=d.get("stop_loss"),
            take_profit=d.get("take_profit"),
            confidence=max(0.0, min(float(d.get("confidence") or 0), 1.0)),
            reasoning=d.get("reasoning", ""),
            source="ai",
        )
        self.last_ai_result = result  # 供引擎寫入決策紀錄
        if decision.action != Action.HOLD and decision.confidence < self.p("min_confidence"):
            decision.reasoning = f"[信心 {decision.confidence:.2f} 低於門檻，改為觀望] {decision.reasoning}"
            decision.action = Action.HOLD
        return decision
