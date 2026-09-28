"""內建規則型策略範例。"""

from __future__ import annotations

from goldhunter.strategies import ta
from goldhunter.strategies.base import Strategy, StrategyContext


class MACrossStrategy(Strategy):
    name = "ma_cross"
    description = "均線交叉：快線上穿慢線做多、下穿平倉（永續可反手做空）；ATR 止損"
    default_params = {"fast": 20, "slow": 50, "size_pct": 20, "atr_mult": 2.0, "allow_short": True, "leverage": 1}
    warmup = 60

    def on_bar(self, ctx: StrategyContext):
        close = ctx.close
        fast, slow = ta.ema(close, self.p("fast")), ta.ema(close, self.p("slow"))
        a = ta.atr(ctx.high, ctx.low, close, 14)[-1] or 0
        pos = ctx.position_size
        k = self.p("atr_mult")
        can_short = self.p("allow_short") and ctx.capabilities.supports_short
        if ta.crossover(fast, slow) and pos <= 0:
            # 空手做多；持有空單時反手（風控會先平空再開多）
            return ctx.long(self.p("size_pct"), stop_loss=ctx.price - k * a, leverage=self.p("leverage"),
                            reasoning="EMA 快線上穿慢線" + ("，空單反手做多" if pos < 0 else ""))
        if ta.crossunder(fast, slow) and pos >= 0:
            if can_short:
                return ctx.short(self.p("size_pct"), stop_loss=ctx.price + k * a, leverage=self.p("leverage"),
                                 reasoning="EMA 快線下穿慢線" + ("，多單反手做空" if pos > 0 else ""))
            if pos > 0:
                return ctx.close_position("快線下穿慢線，多單平倉")
        return None


class RSIReversionStrategy(Strategy):
    name = "rsi_reversion"
    description = "RSI 均值回歸：超賣買進、回到中線平倉"
    default_params = {"length": 14, "oversold": 30, "exit": 55, "size_pct": 20, "stop_pct": 3.0}
    warmup = 30

    def on_bar(self, ctx: StrategyContext):
        r = ta.rsi(ctx.close, self.p("length"))[-1]
        if r is None:
            return None
        if ctx.position_size == 0 and r < self.p("oversold"):
            return ctx.long(self.p("size_pct"), stop_loss=ctx.price * (1 - self.p("stop_pct") / 100),
                            reasoning=f"RSI={r:.1f} 超賣")
        if ctx.position_size > 0 and r > self.p("exit"):
            return ctx.close_position(f"RSI={r:.1f} 回到中線")
        return None
