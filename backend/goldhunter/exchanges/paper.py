"""模擬交易所（Paper Trading）與回測共用的撮合器。

使用真實行情（或回測歷史資料）加上模擬成交：市價單以最新價 ± 滑價成交，並扣手續費。
加密貨幣永續：可放空、可槓桿；股票（規劃中）：不可放空、無槓桿、全額現金交割。
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable

from goldhunter.core.models import (
    Balance,
    Candle,
    Instrument,
    InstrumentType,
    Order,
    OrderRequest,
    OrderStatus,
    Position,
    Side,
)
from goldhunter.exchanges.base import Capabilities, ExchangeAdapter

CandleSource = Callable[[Instrument, str, int], Awaitable[list[Candle]]]


class PaperExchange(ExchangeAdapter):
    id = "paper"

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.0005,
        slippage: float = 0.0005,
        candle_source: CandleSource | None = None,
        quote: str = "USDT",
    ):
        self.cash = initial_cash
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.quote = quote
        self.prices: dict[Instrument, float] = {}
        self.positions: dict[Instrument, Position] = {}
        self.fills: list[Order] = []
        self.realized_pnl = 0.0
        self._candle_source = candle_source
        self._ids = itertools.count(1)

    # ---- 行情 ----
    def set_price(self, instrument: Instrument, price: float) -> None:
        self.prices[instrument] = price
        if pos := self.positions.get(instrument):
            pos.unrealized_pnl = (price - pos.entry_price) * pos.quantity

    def capabilities(self, instrument: Instrument) -> Capabilities:
        if instrument.type == InstrumentType.PERP:
            return Capabilities(supports_short=True, max_leverage=50, taker_fee=self.fee_rate)
        return Capabilities(supports_short=False, max_leverage=1, taker_fee=self.fee_rate)

    async def fetch_candles(self, instrument: Instrument, timeframe: str, limit: int = 200) -> list[Candle]:
        if not self._candle_source:
            raise RuntimeError("PaperExchange 未設定行情來源")
        candles = await self._candle_source(instrument, timeframe, limit)
        if candles:
            self.set_price(instrument, candles[-1].close)
        return candles

    async def fetch_price(self, instrument: Instrument) -> float:
        if instrument not in self.prices:
            await self.fetch_candles(instrument, "1m", 1)
        return self.prices[instrument]

    # ---- 帳戶 ----
    def equity(self) -> float:
        eq = self.cash
        for inst, pos in self.positions.items():
            price = self.prices.get(inst, pos.entry_price)
            if inst.type == InstrumentType.PERP:
                # 永續：保證金已從 cash 扣除，權益＝cash＋保證金＋未實現損益
                eq += abs(pos.quantity) * pos.entry_price / pos.leverage + (price - pos.entry_price) * pos.quantity
            else:
                eq += pos.quantity * price
        return eq

    async def fetch_balance(self) -> Balance:
        return Balance(currency=self.quote, total=self.equity(), free=self.cash)

    async def fetch_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.quantity != 0]

    # ---- 下單 ----
    async def place_order(self, req: OrderRequest) -> Order:
        inst = req.instrument
        price = self.prices.get(inst)
        if price is None:
            price = await self.fetch_price(inst)
        fill_price = price * (1 + self.slippage) if req.side == Side.BUY else price * (1 - self.slippage)
        signed_qty = req.quantity if req.side == Side.BUY else -req.quantity
        pos = self.positions.get(inst)
        is_perp = inst.type == InstrumentType.PERP
        leverage = max(1, req.leverage or (pos.leverage if pos else 1)) if is_perp else 1

        reject = self._validate(req, pos, signed_qty, fill_price, leverage)
        if reject:
            return self._record(req, 0, None, OrderStatus.REJECTED, 0.0, {"reason": reject})

        fee = req.quantity * fill_price * self.fee_rate
        self.cash -= fee
        cur_qty = pos.quantity if pos else 0.0

        # 先處理減倉（與現有部位反向的部分）
        closing = 0.0
        if cur_qty and (cur_qty > 0) != (signed_qty > 0):
            closing = min(abs(signed_qty), abs(cur_qty))
            direction = 1 if cur_qty > 0 else -1
            pnl = (fill_price - pos.entry_price) * closing * direction
            self.realized_pnl += pnl
            if is_perp:
                self.cash += closing * pos.entry_price / pos.leverage + pnl
            else:
                self.cash += closing * fill_price
            pos.quantity -= closing * direction
            if abs(pos.quantity) < 1e-12:
                del self.positions[inst]
                pos = None

        # 再處理加倉 / 開倉
        opening = abs(signed_qty) - closing
        if opening > 1e-12 and not req.reduce_only:
            direction = 1 if signed_qty > 0 else -1
            cost = opening * fill_price / leverage if is_perp else opening * fill_price
            self.cash -= cost
            if pos:
                new_qty = pos.quantity + opening * direction
                pos.entry_price = (abs(pos.quantity) * pos.entry_price + opening * fill_price) / abs(new_qty)
                pos.quantity = new_qty
            else:
                self.positions[inst] = Position(
                    instrument=inst, quantity=opening * direction, entry_price=fill_price, leverage=leverage
                )
        filled = closing + (opening if not req.reduce_only else 0)
        self.set_price(inst, price)
        return self._record(req, filled, fill_price, OrderStatus.FILLED, fee)

    def _validate(self, req, pos, signed_qty, fill_price, leverage) -> str | None:
        inst = req.instrument
        caps = self.capabilities(inst)
        cur_qty = pos.quantity if pos else 0.0
        new_qty = cur_qty + signed_qty
        if new_qty < -1e-12 and not caps.supports_short:
            return "此市場不支援放空"
        if leverage > caps.max_leverage:
            return f"槓桿 {leverage} 超過上限 {caps.max_leverage}"
        if req.reduce_only and (cur_qty == 0 or (cur_qty > 0) == (signed_qty > 0)):
            return "reduce_only 但沒有可減的部位"
        flipped = cur_qty != 0 and new_qty != 0 and (new_qty > 0) != (cur_qty > 0)
        opening = abs(new_qty) if flipped else max(0.0, abs(new_qty) - abs(cur_qty))
        need = opening * fill_price / (leverage if inst.type == InstrumentType.PERP else 1)
        need += req.quantity * fill_price * self.fee_rate
        if not req.reduce_only and need > self.cash + 1e-9:
            return f"資金不足：需要 {need:.2f}，可用 {self.cash:.2f}"
        return None

    def _record(self, req, filled, price, status, fee, raw=None) -> Order:
        order = Order(
            id=f"paper-{next(self._ids)}",
            instrument=req.instrument,
            side=req.side,
            quantity=req.quantity,
            filled=filled,
            avg_price=price,
            status=status,
            fee=fee,
            raw=raw,
        )
        if status == OrderStatus.FILLED:
            self.fills.append(order)
        return order
