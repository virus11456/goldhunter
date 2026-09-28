"""策略介面。

每根 K 棒（或每次輪詢）呼叫一次 ``on_bar(ctx)``，回傳 Decision 或 None（＝不動作）。
同一套策略程式可以用在：模擬交易、實盤、回測。
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from goldhunter.core.models import Action, Balance, Candle, Decision, Instrument, Position
from goldhunter.exchanges.base import Capabilities


class StrategyContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instrument: Instrument
    timeframe: str
    candles: list[Candle]
    position: Position | None
    balance: Balance
    capabilities: Capabilities
    max_leverage: int = 1
    max_size_pct: float = 100.0

    # ---- 方便策略使用的序列（對應 Pine 的 open/high/low/close/volume）----
    @property
    def open(self) -> list[float]:
        return [c.open for c in self.candles]

    @property
    def high(self) -> list[float]:
        return [c.high for c in self.candles]

    @property
    def low(self) -> list[float]:
        return [c.low for c in self.candles]

    @property
    def close(self) -> list[float]:
        return [c.close for c in self.candles]

    @property
    def volume(self) -> list[float]:
        return [c.volume for c in self.candles]

    @property
    def price(self) -> float:
        return self.candles[-1].close

    @property
    def position_size(self) -> float:
        """> 0 多單、< 0 空單、0 空手（對應 Pine 的 strategy.position_size 正負號）"""
        return self.position.quantity if self.position else 0.0

    # ---- 產生決策的捷徑 ----
    def long(self, size_pct: float, stop_loss: float | None = None, take_profit: float | None = None,
             leverage: int = 1, reasoning: str = "", confidence: float = 1.0) -> Decision:
        return Decision(instrument=self.instrument, action=Action.OPEN_LONG, size_pct=size_pct,
                        stop_loss=stop_loss, take_profit=take_profit, leverage=leverage,
                        reasoning=reasoning, confidence=confidence)

    def short(self, size_pct: float, stop_loss: float | None = None, take_profit: float | None = None,
              leverage: int = 1, reasoning: str = "", confidence: float = 1.0) -> Decision:
        return Decision(instrument=self.instrument, action=Action.OPEN_SHORT, size_pct=size_pct,
                        stop_loss=stop_loss, take_profit=take_profit, leverage=leverage,
                        reasoning=reasoning, confidence=confidence)

    def close_position(self, reasoning: str = "") -> Decision:
        return Decision(instrument=self.instrument, action=Action.CLOSE, reasoning=reasoning)


class Strategy(ABC):
    """所有策略的基底類別。

    子類別可定義：
      name / description：顯示用
      default_params：參數預設值（可在設定介面修改）
      warmup：最少需要幾根 K 棒才開始運算
    """

    name: ClassVar[str] = "base"
    description: ClassVar[str] = ""
    default_params: ClassVar[dict[str, Any]] = {}
    warmup: ClassVar[int] = 50
    uses_ai: ClassVar[bool] = False

    def __init__(self, params: dict[str, Any] | None = None, ai=None):
        self.params: dict[str, Any] = {**self.default_params, **(params or {})}
        self.ai = ai

    def p(self, key: str) -> Any:
        return self.params[key]

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> Decision | None:
        """可寫成 def 或 async def"""

    async def run(self, ctx: StrategyContext) -> Decision | None:
        if len(ctx.candles) < self.warmup:
            return None
        result = self.on_bar(ctx)
        if inspect.isawaitable(result):
            result = await result
        return result
