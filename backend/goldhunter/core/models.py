"""跨市場共用的核心資料模型。

設計重點：用 Instrument 統一描述「哪個市場、哪個商品、哪種型態」，
讓加密貨幣、美股、台股共用同一套策略 / 風控 / 下單流程。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Market(StrEnum):
    CRYPTO = "crypto"
    US = "us"  # 美股（P2）
    TW = "tw"  # 台股（P3）


class InstrumentType(StrEnum):
    PERP = "perp"  # 永續合約（加密貨幣只做 USDT 永續）
    STOCK = "stock"  # 美股 / 台股（規劃中）


class Instrument(BaseModel):
    """統一商品代碼，字串格式：``market:symbol:type``

    例：``crypto:BTC/USDT:perp``、``us:AAPL:stock``、``tw:2330:stock``
    加密貨幣只支援永續合約（perp），不做現貨。
    """

    market: Market
    symbol: str
    type: InstrumentType

    model_config = {"frozen": True}

    @classmethod
    def parse(cls, text: str) -> Instrument:
        parts = text.split(":")
        if len(parts) != 3:
            raise ValueError(f"商品代碼格式錯誤：{text!r}，應為 market:symbol:type")
        market, symbol, typ = parts
        if market == Market.CRYPTO.value and typ != InstrumentType.PERP.value:
            raise ValueError("加密貨幣只支援永續合約，格式如 crypto:BTC/USDT:perp")
        try:
            inst = cls(market=Market(market), symbol=symbol, type=InstrumentType(typ))
        except ValueError as e:
            raise ValueError(f"商品代碼格式錯誤：{text!r}（{e}）") from e
        return inst

    def __str__(self) -> str:
        return f"{self.market.value}:{self.symbol}:{self.type.value}"

    @property
    def ccxt_symbol(self) -> str:
        """轉成 ccxt 格式：USDT 永續 BTC/USDT → BTC/USDT:USDT"""
        if self.type == InstrumentType.PERP and ":" not in self.symbol:
            quote = self.symbol.split("/")[-1]
            return f"{self.symbol}:{quote}"
        return self.symbol


class Candle(BaseModel):
    ts: int  # 毫秒 timestamp（K 線開盤時間）
    open: float
    high: float
    low: float
    close: float
    volume: float


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderRequest(BaseModel):
    instrument: Instrument
    side: Side
    quantity: float = Field(gt=0)
    type: OrderType = OrderType.MARKET
    price: float | None = None
    reduce_only: bool = False
    leverage: int | None = None
    client_tag: str | None = None


class OrderStatus(StrEnum):
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class Order(BaseModel):
    id: str
    instrument: Instrument
    side: Side
    quantity: float
    filled: float = 0.0
    avg_price: float | None = None
    status: OrderStatus
    fee: float = 0.0
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw: dict | None = None


class Position(BaseModel):
    instrument: Instrument
    quantity: float  # 正數＝多單、負數＝空單
    entry_price: float
    leverage: int = 1
    unrealized_pnl: float = 0.0

    @property
    def side(self) -> Literal["long", "short", "flat"]:
        if self.quantity > 0:
            return "long"
        if self.quantity < 0:
            return "short"
        return "flat"

    def notional(self, price: float) -> float:
        return abs(self.quantity) * price


class Balance(BaseModel):
    currency: str
    total: float  # 權益（含未實現損益）
    free: float  # 可用資金


class Action(StrEnum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE = "close"
    HOLD = "hold"


class Decision(BaseModel):
    """策略（規則 / AI / TradingView）統一輸出的交易決策，必須先經過風控才會下單。"""

    instrument: Instrument
    action: Action
    size_pct: float = Field(default=0.0, ge=0, le=100, description="佔帳戶權益百分比")
    quantity: float | None = Field(default=None, gt=0, description="固定數量（幣的數量）；有填時優先於 size_pct，例如分批加碼策略")
    close_pct: float = Field(default=100.0, gt=0, le=100, description="action=close 時平掉持倉的百分比")
    leverage: int = Field(default=1, ge=1, le=125)
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    reasoning: str = ""
    source: str = "strategy"  # strategy / ai / tradingview / manual
    meta: dict | None = None  # 附加資訊（例如 AI 交易員的大師與審查委員意見），會一併寫入決策紀錄
