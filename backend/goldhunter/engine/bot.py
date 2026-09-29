"""Bot 執行引擎：行情 → 策略 → AI 副駕駛 → 風控 → 下單 → 紀錄。

- 策略只在「新 K 棒收盤」時運算一次（與 TradingView 預設行為一致，也避免重複呼叫 AI）
- AI 副駕駛（copilot）：
    A. 訊號審核：策略的開倉訊號先給 AI 結合新聞 / 總經 / 合約數據判斷，可放行、否決、微調
    B. 持倉管理：持倉中定期檢查，只能做降低風險的動作
    C. 參數微調：定期提出新參數，樣本外回測較佳才套用（或等你確認）
- 對照組（baseline）：同一策略「不經 AI」的訊號在模擬帳本上同步執行，用來比較 AI 的貢獻
- 止損 / 止盈由引擎監控：每次輪詢檢查最新價，觸發即市價平倉
- TradingView / 手動訊號走同一條 handle_decision 流程，同樣必須通過風控
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlmodel import Session, col, select

from goldhunter.ai.base import AIProvider, AIProviderError
from goldhunter.analysis.config import EntryConfig
from goldhunter.analysis.entry import EntryAnalysis, analyze_entry, strategy_signal_indices
from goldhunter.copilot.config import CopilotConfig
from goldhunter.copilot.review import manage_position, review_signal
from goldhunter.copilot.tune import tune_strategy
from goldhunter.core.models import Action, Decision, Instrument, OrderStatus, Position
from goldhunter.engine.universe import UniverseRules, pick, to_instruments
from goldhunter.exchanges.base import ExchangeAdapter
from goldhunter.exchanges.paper import PaperExchange
from goldhunter.intel.hub import IntelSnapshot, hub
from goldhunter.risk.manager import RiskConfig, RiskState, evaluate
from goldhunter.store.db import Bot, DecisionLog, EquitySnapshot, StrategyConfig, Trade, TuningRun, get_engine
from goldhunter.strategies.base import Strategy, StrategyContext

log = logging.getLogger("goldhunter.bot")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PendingEntry(BaseModel):
    """等待中的進場掛單（由引擎監控：價格碰到就市價進場，所有交易所行為一致）"""

    model_config = {"arbitrary_types_allowed": True}
    decision: Decision
    level: float
    label: str
    bars_left: int
    ai_result: object | None = None
    extra: dict = {}
    extra_reasons: list[str] = []


class StopLevels(BaseModel):
    stop_loss: float | None = None
    take_profit: float | None = None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _hit_stop(pos: Position, lv: StopLevels, price: float) -> str | None:
    long = pos.quantity > 0
    if lv.stop_loss is not None and ((long and price <= lv.stop_loss) or (not long and price >= lv.stop_loss)):
        return f"觸發止損 {lv.stop_loss:.6g}（現價 {price:.6g}）"
    if lv.take_profit is not None and ((long and price >= lv.take_profit) or (not long and price <= lv.take_profit)):
        return f"觸發止盈 {lv.take_profit:.6g}（現價 {price:.6g}）"
    return None


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
        copilot: CopilotConfig | None = None,
        exchange_id: str = "binance",
        strategy_id: int | None = None,
        universe: dict | None = None,
        entry: dict | None = None,
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
        self.copilot = copilot or CopilotConfig()
        self.exchange_id = exchange_id.split(":")[-1]
        self.strategy_id = strategy_id
        self.universe = UniverseRules(**(universe or {}))
        self.entry = EntryConfig(**(entry or {}))
        self.pending: dict[Instrument, PendingEntry] = {}
        self.base_instruments = list(instruments)
        self.universe_at: datetime | None = None
        self.risk_state = RiskState()
        self.stops: dict[Instrument, StopLevels] = {}
        self.last_bar: dict[Instrument, int] = {}
        self.last_price: dict[Instrument, float] = {}
        self.last_manage: dict[Instrument, datetime] = {}
        self.last_tune: datetime | None = self._last_tune_time()
        self._tune_task: asyncio.Task | None = None
        # 對照組：AI 副駕駛會改變交易時才需要
        self.baseline: PaperExchange | None = None
        self.baseline_stops: dict[Instrument, StopLevels] = {}
        self.baseline_risk = RiskState()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self.last_run_at: datetime | None = None

    # ---------- 屬性 ----------
    @property
    def copilot_active(self) -> bool:
        """AI 副駕駛是否會介入規則策略的交易（純 AI 策略本身就是 AI，不另外審核）"""
        return bool(self.ai and self.strategy and not self.strategy.uses_ai
                    and (self.copilot.review or self.copilot.manage))

    # ---------- 生命週期 ----------
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._loop(), name=f"bot-{self.bot_id}")

    async def stop(self) -> None:
        for t in (self._task, self._tune_task):
            if t:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._task = self._tune_task = None
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
            self._persist_status()
            await asyncio.sleep(self.interval_sec)

    # ---------- 主流程 ----------
    async def tick(self) -> None:
        async with self._lock:
            self.last_run_at = _utcnow()
            await self._refresh_universe()
            if self.copilot_active and self.baseline is None:
                # 對照組必須在第一個訊號之前建立，才能公平比較
                start_equity = (await self.exchange.fetch_balance()).total
                self.baseline = PaperExchange(initial_cash=start_equity, fee_rate=0.0005, slippage=0.0005)
            for inst in self.instruments:
                need = max(300, (self.strategy.warmup if self.strategy is not None else 0) + 10)
                candles = await self.exchange.fetch_candles(inst, self.timeframe, need)
                if len(candles) < 2:
                    continue
                price = candles[-1].close
                self.last_price[inst] = price
                if self.baseline:
                    self.baseline.set_price(inst, price)
                    await self._baseline_check_stops(inst, price)
                await self._check_stops(inst, price)
                await self._check_pending(inst, candles)

                closed = candles[:-1]  # 最後一根尚未收盤
                pos = await self._position(inst)
                if pos and self.copilot_active and self.copilot.manage:
                    await self._maybe_manage(inst, pos, closed, price)

                if self.strategy is None or self.last_bar.get(inst) == closed[-1].ts:
                    continue
                self.last_bar[inst] = closed[-1].ts
                ctx = await self._context(inst, closed)
                if self.strategy.uses_ai:
                    ctx.intel = (await self._intel(inst)).to_prompt()
                    ctx.recent_pnls = self._recent_pnls()
                decision = await self.strategy.run(ctx)
                if decision is None:
                    continue
                ai_result = getattr(self.strategy, "last_ai_result", None)
                self.strategy.last_ai_result = None  # type: ignore[attr-defined]
                if self.copilot_active:
                    await self._baseline_apply(decision, price)
                await self._process_signal(decision, ctx, price, ai_result)

            balance = await self.exchange.fetch_balance()
            self._save(EquitySnapshot(bot_id=self.bot_id, equity=balance.total,
                                      baseline_equity=self.baseline.equity() if self.baseline else None))
        self._maybe_start_tune()

    async def handle_decision(self, decision: Decision) -> DecisionLog:
        """外部訊號（TradingView / 手動）進入點"""
        async with self._lock:
            price = await self.exchange.fetch_price(decision.instrument)
            self.last_price[decision.instrument] = price
            return await self._handle(decision, price=price)

    # ---------- 標的範圍 ----------
    async def _refresh_universe(self) -> None:
        if self.universe.mode != "rules":
            return
        now = _utcnow()
        if self.universe_at and now - self.universe_at < timedelta(hours=self.universe.refresh_hours):
            return
        try:
            picked = to_instruments(pick(await self.exchange.perp_volumes(), self.universe))
        except Exception as e:
            log.warning("bot %s 標的規則更新失敗，沿用原清單：%s", self.bot_id, e)
            self.universe_at = now
            return
        held = [p.instrument for p in await self.exchange.fetch_positions() if p.instrument not in picked]
        self.instruments = picked + held  # 持倉中的標的繼續管理到平倉
        self.universe_at = now

    # ---------- AI 副駕駛 ----------
    async def _intel(self, inst: Instrument) -> IntelSnapshot:
        try:
            return await hub.snapshot(self.exchange_id, inst)
        except Exception as e:
            log.warning("市場情報取得失敗：%s", e)
            return IntelSnapshot(instrument=str(inst), fetched_at=_utcnow().strftime("%Y-%m-%d %H:%M"))

    async def _process_signal(self, decision: Decision, ctx: StrategyContext, price: float, ai_result=None) -> None:
        opening = decision.action in (Action.OPEN_LONG, Action.OPEN_SHORT)
        if not opening:
            if decision.action == Action.CLOSE:
                self.pending.pop(decision.instrument, None)  # 出場訊號取消尚未成交的進場掛單
            await self._handle(decision, price=price, ai_result=ai_result)
            return
        intel: IntelSnapshot | None = None
        # 重大經濟事件前後暫停開倉（純規則，不需 AI）
        if self.copilot.event_blackout_min > 0:
            intel = await self._intel(ctx.instrument)
            if ev := intel.upcoming_event_within(self.copilot.event_blackout_min):
                self._log_rejected(decision, [f"事件避險：{ev['time']} {ev['title']} 前後 "
                                              f"{self.copilot.event_blackout_min} 分鐘不開新倉"])
                return
        if self.copilot_active and self.copilot.review:
            intel = intel or await self._intel(ctx.instrument)
            try:
                out = await review_signal(self.ai, decision, ctx, intel, self.copilot, self._recent_pnls())  # type: ignore[arg-type]
            except AIProviderError as e:
                self._log_rejected(decision, [f"AI 審核失敗，保守不開倉：{e}"])
                return
            review_info = {"verdict": out.verdict, "reasoning": out.reasoning, "notes": out.notes,
                           "original": decision.model_dump(mode="json")}
            if out.decision is None:
                self._log_rejected(decision, [f"AI 否決：{out.reasoning}", *out.notes], ai_result=out.ai,
                                   extra={"copilot": review_info})
                return
            await self._open(out.decision, price=price, closed=ctx.candles, ai_result=out.ai,
                             extra_reasons=[f"AI {'調整' if out.verdict == 'adjust' else '放行'}", *out.notes],
                             extra={"copilot": review_info})
            return
        await self._open(decision, price=price, closed=ctx.candles, ai_result=ai_result)

    # ---------- 進場分析與掛單等待 ----------
    async def _analyze(self, decision: Decision, closed) -> EntryAnalysis | None:
        direction = "long" if decision.action == Action.OPEN_LONG else "short"
        try:
            hist = (await self.exchange.fetch_candles(decision.instrument, self.timeframe, 1000))[:-1]
            if len(hist) < len(closed):
                hist = closed
            sig: list[int] = []
            if self.strategy is not None and not self.strategy.uses_ai:
                sig = await strategy_signal_indices(type(self.strategy)(dict(self.strategy.params)),
                                                    decision.instrument, self.timeframe, hist, direction)
            return analyze_entry(hist, self.timeframe, direction, decision.stop_loss, decision.take_profit, sig)
        except Exception as e:
            log.warning("bot %s 進場分析失敗：%s", self.bot_id, e)
            return None

    async def _open(self, decision: Decision, *, price: float, closed, ai_result=None,
                    extra_reasons: list[str] | None = None, extra: dict | None = None) -> None:
        inst = decision.instrument
        self.pending.pop(inst, None)  # 新訊號取代舊的掛單
        analysis = await self._analyze(decision, closed)
        extra = dict(extra or {})
        if analysis:
            extra["entry_analysis"] = analysis.model_dump()
        smart = self.entry.mode == "smart" and analysis is not None
        if smart and analysis.recommended == "skip" and self.entry.skip_negative_ev:
            self._log_rejected(decision, [f"進場分析：{analysis.recommendation}", *(extra_reasons or [])],
                               ai_result=ai_result, extra=extra)
            return
        if smart and analysis.recommended not in ("market", "skip"):
            cand = next(c for c in analysis.candidates if c.key == analysis.recommended)
            d = decision.model_copy()
            d.stop_loss = d.stop_loss or analysis.stop
            d.take_profit = d.take_profit or analysis.target
            wait = self.entry.max_wait_bars or analysis.wait_bars
            self.pending[inst] = PendingEntry(decision=d, level=cand.price, label=cand.label, bars_left=wait,
                                              ai_result=ai_result, extra=extra,
                                              extra_reasons=[*(extra_reasons or []), f"掛單成交：{cand.label} {cand.price:.6g}"])
            self._log_rejected(decision, [f"等待進場：{cand.label} {cand.price:.6g}（盈虧比 1:{cand.rr}、"
                                          f"約 {cand.fill_prob:.0%} 機率等得到，{wait} 根 K 棒內沒成交就取消）",
                                          *(extra_reasons or [])],
                               ai_result=ai_result, extra={**extra, "pending": True})
            return
        await self._handle(decision, price=price, ai_result=ai_result, extra_reasons=extra_reasons, extra=extra)

    async def _check_pending(self, inst: Instrument, candles) -> None:
        pe = self.pending.get(inst)
        if not pe:
            return
        bar, price = candles[-1], candles[-1].close
        long = pe.decision.action == Action.OPEN_LONG
        touched = (long and bar.low <= pe.level) or (not long and bar.high >= pe.level)
        if touched:
            self.pending.pop(inst, None)
            await self._handle(pe.decision, price=price, ai_result=pe.ai_result, extra_reasons=pe.extra_reasons,
                               extra=pe.extra)
            return
        if self.last_bar.get(inst) != candles[-2].ts:  # 每根新 K 棒扣一次
            pe.bars_left -= 1
        if pe.bars_left <= 0:
            self.pending.pop(inst, None)
            self._log_rejected(pe.decision, [f"掛單逾時未成交，已取消（{pe.label} {pe.level:.6g}）"], extra=pe.extra)

    async def _maybe_manage(self, inst: Instrument, pos: Position, closed, price: float) -> None:
        last = self.last_manage.get(inst)
        if last and _utcnow() - last < timedelta(minutes=self.copilot.manage_interval_min):
            return
        self.last_manage[inst] = _utcnow()
        lv = self.stops.get(inst) or StopLevels()
        ctx = await self._context(inst, closed)
        try:
            out = await manage_position(self.ai, pos, lv.stop_loss, lv.take_profit, ctx, await self._intel(inst),  # type: ignore[arg-type]
                                        self.copilot)
        except AIProviderError as e:
            log.warning("AI 持倉管理失敗：%s", e)
            return
        if out.action == "close":
            await self._handle(Decision(instrument=inst, action=Action.CLOSE, reasoning=f"AI 持倉管理：{out.reasoning}",
                                        source="copilot"), price=price, ai_result=out.ai, extra_reasons=out.notes)
        elif out.action == "reduce":
            await self._handle(Decision(instrument=inst, action=Action.CLOSE, close_pct=out.reduce_pct or 50,
                                        reasoning=f"AI 減倉 {out.reduce_pct}%：{out.reasoning}", source="copilot"),
                               price=price, ai_result=out.ai, extra_reasons=out.notes)
        else:
            if out.action == "move_stop":
                self.stops[inst] = StopLevels(stop_loss=out.new_stop, take_profit=lv.take_profit)
            self._save(DecisionLog(
                bot_id=self.bot_id, instrument=str(inst), source="copilot", action=out.action,
                decision={"new_stop": out.new_stop, "reasoning": out.reasoning}, approved=out.action == "move_stop",
                reasons=[f"AI 持倉管理：{out.reasoning}", *out.notes], **self._ai_fields(out.ai)))

    def _maybe_start_tune(self) -> None:
        if not (self.ai and self.strategy and self.copilot.tune and not self.strategy.uses_ai):
            return
        if self._tune_task and not self._tune_task.done():
            return
        if self.last_tune and _utcnow() - self.last_tune < timedelta(hours=self.copilot.tune_interval_hours):
            return
        self.last_tune = _utcnow()
        self._tune_task = asyncio.create_task(self.run_tune(), name=f"tune-{self.bot_id}")

    async def run_tune(self) -> TuningRun:
        """C. 參數微調（背景執行，不阻塞交易）"""
        assert self.strategy is not None and self.ai is not None
        inst = self.instruments[0]
        run = TuningRun(bot_id=self.bot_id, current_params=dict(self.strategy.params))
        try:
            candles = await self._history(inst, self.copilot.tune_lookback_bars)
            out = await tune_strategy(self.ai, type(self.strategy), dict(self.strategy.params), inst, self.timeframe,
                                      candles, self.copilot, self.risk)
            run.proposed_params, run.current_metrics, run.proposed_metrics = (
                out.proposed_params, out.current_metrics, out.proposed_metrics)
            run.reasoning, run.note = out.reasoning, out.note
            run.status = "proposed" if out.passed else "rejected"
            if out.passed and self.copilot.tune_auto_apply:
                self.apply_params(out.proposed_params)
                run.status = "applied"
        except Exception as e:
            run.status, run.note = "failed", f"{type(e).__name__}: {e}"
            log.warning("bot %s 參數微調失敗：%s", self.bot_id, e)
        return self._save(run)

    def apply_params(self, params: dict) -> None:
        assert self.strategy is not None
        self.strategy.params.update(params)
        with Session(get_engine()) as s:
            bot = s.get(Bot, self.bot_id)
            st = s.get(StrategyConfig, self.strategy_id) if self.strategy_id else None
            if bot:
                base = dict(st.params or {}) if st else {}
                bot.params_override = {k: v for k, v in self.strategy.params.items() if base.get(k) != v}
                s.add(bot)
                s.commit()

    async def _history(self, inst: Instrument, bars: int):
        candles = await self.exchange.fetch_candles(inst, self.timeframe, min(bars, 1500))
        return candles[:-1]

    # ---------- 對照組（不經 AI） ----------
    async def _baseline_apply(self, decision: Decision, price: float) -> None:
        if not self.baseline:
            return
        inst = decision.instrument
        self.baseline.set_price(inst, price)
        pos = self.baseline.positions.get(inst)
        res = evaluate(decision, config=self.risk, state=self.baseline_risk, equity=self.baseline.equity(),
                       price=price, position=pos, all_positions=list(self.baseline.positions.values()),
                       capabilities=self.baseline.capabilities(inst), round_qty=self.exchange.round_qty)
        if not res.approved:
            return
        self.baseline_risk.record_orders(len(res.orders))
        for req in res.orders:
            await self.baseline.place_order(req)
        adj = res.adjusted or decision
        if inst in self.baseline.positions and decision.action in (Action.OPEN_LONG, Action.OPEN_SHORT):
            self.baseline_stops[inst] = StopLevels(stop_loss=adj.stop_loss, take_profit=adj.take_profit)
        elif inst not in self.baseline.positions:
            self.baseline_stops.pop(inst, None)

    async def _baseline_check_stops(self, inst: Instrument, price: float) -> None:
        assert self.baseline is not None
        pos = self.baseline.positions.get(inst)
        lv = self.baseline_stops.get(inst)
        if pos and lv and _hit_stop(pos, lv, price):
            await self._baseline_apply(Decision(instrument=inst, action=Action.CLOSE), price)

    # ---------- 內部 ----------
    async def _context(self, inst: Instrument, closed) -> StrategyContext:
        return StrategyContext(
            instrument=inst, timeframe=self.timeframe, candles=closed, position=await self._position(inst),
            balance=await self.exchange.fetch_balance(), capabilities=self.exchange.capabilities(inst),
            max_leverage=self.risk.max_leverage, max_size_pct=self.risk.max_position_pct,
        )

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
        if hit := _hit_stop(pos, lv, price):
            await self._handle(Decision(instrument=inst, action=Action.CLOSE, reasoning=hit, source="stop"), price=price)

    def _recent_pnls(self, n: int = 10) -> list[float]:
        with Session(get_engine()) as s:
            rows = s.exec(select(Trade.realized_pnl).where(Trade.bot_id == self.bot_id, col(Trade.realized_pnl).is_not(None))
                          .order_by(col(Trade.ts).desc()).limit(n)).all()
        return [float(x) for x in reversed(rows)]

    @staticmethod
    def _ai_fields(ai_result) -> dict:
        return {"ai_model": getattr(ai_result, "model", None), "ai_raw": getattr(ai_result, "raw_text", None),
                "input_tokens": getattr(ai_result, "input_tokens", 0) or 0,
                "output_tokens": getattr(ai_result, "output_tokens", 0) or 0}

    def _log_rejected(self, decision: Decision, reasons: list[str], ai_result=None, extra: dict | None = None) -> None:
        self._save(DecisionLog(
            bot_id=self.bot_id, instrument=str(decision.instrument), source=decision.source,
            action=decision.action.value, decision={**decision.model_dump(mode="json"), **(extra or {})},
            approved=False, reasons=reasons, **self._ai_fields(ai_result)))

    async def _handle(self, decision: Decision, *, price: float, ai_result=None, extra_reasons: list[str] | None = None,
                      extra: dict | None = None) -> DecisionLog:
        inst = decision.instrument
        balance = await self.exchange.fetch_balance()
        positions = await self.exchange.fetch_positions()
        pos = next((p for p in positions if p.instrument == inst), None)
        result = evaluate(
            decision, config=self.risk, state=self.risk_state, equity=balance.total, price=price, position=pos,
            all_positions=positions, capabilities=self.exchange.capabilities(inst), round_qty=self.exchange.round_qty,
            market_open=await self.exchange.is_market_open(inst),
        )
        entry = self._save(DecisionLog(
            bot_id=self.bot_id, instrument=str(inst), source=decision.source, action=decision.action.value,
            decision={**(result.adjusted or decision).model_dump(mode="json"), **(extra or {})},
            approved=result.approved, reasons=[*(extra_reasons or []), *result.reasons], **self._ai_fields(ai_result),
        ))
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
                pos = await self._position(inst)
                if pos is None:
                    self.stops.pop(inst, None)
            else:
                adj = result.adjusted or decision
                self.stops[inst] = StopLevels(stop_loss=adj.stop_loss, take_profit=adj.take_profit)
        return entry

    def _last_tune_time(self) -> datetime | None:
        try:
            with Session(get_engine()) as s:
                row = s.exec(select(TuningRun).where(TuningRun.bot_id == self.bot_id)
                             .order_by(col(TuningRun.ts).desc())).first()
                return _aware(row.ts) if row else None
        except Exception:
            return None

    def _persist_status(self) -> None:
        with Session(get_engine()) as s:
            if bot := s.get(Bot, self.bot_id):
                bot.last_run_at, bot.last_error = self.last_run_at, self.last_error
                s.add(bot)
                s.commit()

    def _save(self, obj):
        with Session(get_engine()) as s:
            s.add(obj)
            s.commit()
            s.refresh(obj)
            return obj
