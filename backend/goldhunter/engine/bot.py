"""Bot 執行引擎：行情 → 策略 → 決策 → 風控 → 下單 → 紀錄。

- 策略只在「新 K 棒收盤」時運算一次（與 TradingView 預設行為一致，也避免每分鐘重複呼叫 AI）
- 止損 / 止盈由引擎監控：每次輪詢檢查最新價，觸發即市價平倉（所有交易所行為一致）
- TradingView / 手動訊號走同一條 handle_decision 流程，同樣必須通過風控
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from pydantic import BaseModel
from sqlmodel import Session

from goldhunter.ai.base import AIProvider
from goldhunter.core.models import Action, Decision, Instrument, OrderStatus, Position, Side
from goldhunter.exchanges.base import ExchangeAdapter
from goldhunter.risk.manager import RiskConfig, RiskState, evaluate
from goldhunter.store.db import DecisionLog, EquitySnapshot, Trade, get_engine
from goldhunter.strategies.base import Strategy, StrategyContext

log = logging.getLogger("goldhunter.bot")


class StopLevels(BaseModel):
    side: str  # long / short
    stop_loss: float | None = None
    take_profit: float | None = None


class BotRunner:
    def __init__(
        self,
        bot_id: int,
        name: str,
        exchange: ExchangeAdapter,
        strategy: Strategy | None,
        instruments: list[Instrument],
        timeframe: str,
        interval_sec: int,
        risk: RiskConfig,
        ai: AIProvider | None = None,
    ):
        self.bot_id = bot_id
        self.name = name
        self.exchange = exchange
        self.strategy = strategy  # None＝純 TradingView / 手動訊號
        self.instruments = instruments
        self.timeframe = timeframe
        self.interval_sec = max(5, interval_sec)
        self.risk = risk
        self.ai = ai
        self.risk_state = RiskState()
        self.stops: dict[Instrument, StopLevels] = {}
        self.last_bar: dict[Instrument, int] = {}
        self.last_price: dict[Instrument, float] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self.last_run_at: datetime | None = None

    # ---------- 生命週期 ----------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._loop(), name=f"bot-{self.bot_id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.exchange.close()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 單次失敗不中斷 bot，記錄後下一輪重試
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("bot %s tick 失敗", self.bot_id)
            await asyncio.sleep(self.interval_sec)

    # ---------- 主流程 ----------
    async def tick(self) -> None:
        async with self._lock:
            self.last_run_at = datetime.utcnow()
            for inst in self.instruments:
                candles = await self.exchange.fetch_candles(inst, self.timeframe, 300)
                if len(candles) < 2:
                    continue
                self.last_price[inst] = candles[-1].close
                await self._check_stops(inst, candles[-1].close)

                closed = candles[:-1]  # 最後一根尚未收盤
                if self.strategy is None or self.last_bar.get(inst) == closed[-1].ts:
                    continue
                self.last_bar[inst] = closed[-1].ts
                balance = await self.exchange.fetch_balance()
                pos = await self._position(inst)
                ctx = StrategyContext(
                    instrument=inst, timeframe=self.timeframe, candles=closed, position=pos, balance=balance,
                    capabilities=self.exchange.capabilities(inst),
                    max_leverage=self.risk.max_leverage, max_size_pct=self.risk.max_position_pct,
                )
                decision = await self.strategy.run(ctx)
                if decision is not None:
                    ai_result = getattr(self.strategy, "last_ai_result", None)
                    self.strategy.last_ai_result = None  # type: ignore[attr-defined]
                    await self._handle(decision, ai_result=ai_result, price=candles[-1].close)
            balance = await self.exchange.fetch_balance()
            self._save(EquitySnapshot(bot_id=self.bot_id, equity=balance.total))

    async def handle_decision(self, decision: Decision) -> DecisionLog:
        """外部訊號（TradingView / 手動）進入點"""
        async with self._lock:
            price = await self.exchange.fetch_price(decision.instrument)
            self.last_price[decision.instrument] = price
            return await self._handle(decision, price=price)

    # ---------- 內部 ----------
    async def _position(self, inst: Instrument) -> Position | None:
        for p in await self.exchange.fetch_positions():
            if p.instrument == inst:
                return p
        return None

    async def _check_stops(self, inst: Instrument, price: float) -> None:
        lv = self.stops.get(inst)
        if not lv:
            return
        pos = await self._position(inst)
        if not pos:
            self.stops.pop(inst, None)
            return
        long = pos.quantity > 0
        hit = None
        if lv.stop_loss is not None and ((long and price <= lv.stop_loss) or (not long and price >= lv.stop_loss)):
            hit = f"觸發止損 {lv.stop_loss}（現價 {price}）"
        elif lv.take_profit is not None and ((long and price >= lv.take_profit) or (not long and price <= lv.take_profit)):
            hit = f"觸發止盈 {lv.take_profit}（現價 {price}）"
        if hit:
            await self._handle(Decision(instrument=inst, action=Action.CLOSE, reasoning=hit, source="stop"), price=price)

    async def _handle(self, decision: Decision, *, price: float, ai_result=None) -> DecisionLog:
        inst = decision.instrument
        balance = await self.exchange.fetch_balance()
        positions = await self.exchange.fetch_positions()
        pos = next((p for p in positions if p.instrument == inst), None)
        result = evaluate(
            decision, config=self.risk, state=self.risk_state, equity=balance.total, price=price, position=pos,
            all_positions=positions, capabilities=self.exchange.capabilities(inst), round_qty=self.exchange.round_qty,
            market_open=await self.exchange.is_market_open(inst),
        )
        entry = DecisionLog(
            bot_id=self.bot_id, instrument=str(inst), source=decision.source, action=decision.action.value,
            decision=(result.adjusted or decision).model_dump(mode="json"), approved=result.approved,
            reasons=result.reasons,
            ai_model=getattr(ai_result, "model", None), ai_raw=getattr(ai_result, "raw_text", None),
            input_tokens=getattr(ai_result, "input_tokens", 0), output_tokens=getattr(ai_result, "output_tokens", 0),
        )
        entry = self._save(entry)
        if not result.approved:
            return entry

        self.risk_state.record_orders(len(result.orders))
        for req in result.orders:
            entry_price = pos.entry_price if pos else None
            pos_qty = pos.quantity if pos else 0.0
            try:
                order = await self.exchange.place_order(req)
            except Exception as e:
                self._save(Trade(bot_id=self.bot_id, decision_id=entry.id, instrument=str(inst), side=req.side.value,
                                 quantity=req.quantity, price=None, status="error", source=decision.source,
                                 reduce_only=req.reduce_only, note=f"{type(e).__name__}: {e}"))
                raise
            pnl = None
            if req.reduce_only and entry_price and order.avg_price:
                direction = 1 if pos_qty > 0 else -1
                pnl = (order.avg_price - entry_price) * order.filled * direction - order.fee
            self._save(Trade(
                bot_id=self.bot_id, decision_id=entry.id, instrument=str(inst), side=req.side.value,
                quantity=order.filled or req.quantity, price=order.avg_price, fee=order.fee,
                reduce_only=req.reduce_only, realized_pnl=pnl, order_id=order.id, status=order.status.value,
                source=decision.source, note=(order.raw or {}).get("reason"),
            ))
            if order.status == OrderStatus.REJECTED:
                break
            if req.reduce_only:
                self.stops.pop(inst, None)
                pos = None
            else:
                adj = result.adjusted or decision
                self.stops[inst] = StopLevels(side="long" if req.side == Side.BUY else "short",
                                              stop_loss=adj.stop_loss, take_profit=adj.take_profit)
        return entry

    def _save(self, obj):
        with Session(get_engine()) as s:
            s.add(obj)
            s.commit()
            s.refresh(obj)
            return obj
