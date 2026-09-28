"""交易所 / 券商統一介面。

加密貨幣、美股、台股都實作同一個 ExchangeAdapter，並用 Capabilities
宣告各市場的限制（能否放空、槓桿上限、最小單位、交易時段），讓風控與策略可以通用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from goldhunter.core.models import Balance, Candle, Instrument, Order, OrderRequest, Position


class Capabilities(BaseModel):
    supports_short: bool = False
    max_leverage: int = 1
    fractional: bool = True  # 是否可下小數數量
    min_qty: float = 0.0
    qty_step: float = 0.0  # 0＝不限制
    always_open: bool = True  # 24/7 市場
    taker_fee: float = 0.0005


class ExchangeAdapter(ABC):
    """所有交易所的共同介面（全部為 async）。"""

    id: str = "base"

    @abstractmethod
    def capabilities(self, instrument: Instrument) -> Capabilities: ...

    @abstractmethod
    async def fetch_candles(self, instrument: Instrument, timeframe: str, limit: int = 200) -> list[Candle]: ...

    @abstractmethod
    async def fetch_price(self, instrument: Instrument) -> float: ...

    @abstractmethod
    async def fetch_balance(self) -> Balance: ...

    @abstractmethod
    async def fetch_positions(self) -> list[Position]: ...

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> Order: ...

    async def cancel_order(self, order_id: str, instrument: Instrument) -> None:
        raise NotImplementedError

    async def is_market_open(self, instrument: Instrument) -> bool:
        return self.capabilities(instrument).always_open

    async def close(self) -> None:  # noqa: B027 — 預設不需釋放資源
        """釋放連線資源"""

    def round_qty(self, instrument: Instrument, qty: float) -> float:
        caps = self.capabilities(instrument)
        if caps.qty_step > 0:
            qty = (qty // caps.qty_step) * caps.qty_step
        elif not caps.fractional:
            qty = float(int(qty))
        return qty if qty >= caps.min_qty else 0.0
