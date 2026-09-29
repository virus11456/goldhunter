"""風控：所有決策（規則 / AI / TradingView / 手動）下單前都必須通過這裡。

原則：減倉（平倉）永遠放行；開倉才檢查。檢查失敗就拒絕並記錄原因，不會「自作主張」改方向。
"""

from __future__ import annotations

import time
from collections import deque

from pydantic import BaseModel, Field

from goldhunter.core.models import Action, Decision, OrderRequest, Position, Side
from goldhunter.exchanges.base import Capabilities


class RiskConfig(BaseModel):
    max_position_pct: float = Field(default=20.0, description="單筆倉位上限（佔權益 %，未含槓桿）")
    max_total_exposure_pct: float = Field(default=100.0, description="總曝險上限（名目價值佔權益 %，含槓桿）")
    max_leverage: int = Field(default=3, ge=1)
    daily_loss_limit_pct: float = Field(default=5.0, description="單日虧損達此 % 即熔斷，當日停止開倉")
    require_stop_loss: bool = True
    default_stop_loss_pct: float | None = Field(default=3.0, description="決策未帶止損時自動補上；None＝直接拒絕")
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    max_orders_per_hour: int = 20
    allow_pyramiding: bool = False  # 同方向是否允許加碼
    max_positions: int = Field(default=0, ge=0, description="最多同時持有幾個標的；0＝不限")
    long_only: bool = False  # 只做多（例如價值投資風格的大師）


class RiskResult(BaseModel):
    approved: bool
    orders: list[OrderRequest] = []
    reasons: list[str] = []
    adjusted: Decision | None = None


class RiskState:
    """每個 Bot 各自一份：當日起始權益、近一小時下單時間"""

    def __init__(self):
        self.day: str | None = None
        self.day_start_equity: float | None = None
        self.order_times: deque[float] = deque()
        self.halted_reason: str | None = None

    def roll_day(self, equity: float, now: float | None = None) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(now or time.time()))
        if day != self.day:
            self.day, self.day_start_equity, self.halted_reason = day, equity, None

    def record_orders(self, n: int, now: float | None = None) -> None:
        t = now or time.time()
        self.order_times.extend([t] * n)

    def orders_last_hour(self, now: float | None = None) -> int:
        t = now or time.time()
        while self.order_times and t - self.order_times[0] > 3600:
            self.order_times.popleft()
        return len(self.order_times)


def evaluate(
    decision: Decision,
    *,
    config: RiskConfig,
    state: RiskState,
    equity: float,
    price: float,
    position: Position | None,
    all_positions: list[Position],
    capabilities: Capabilities,
    round_qty,
    market_open: bool = True,
    now: float | None = None,
) -> RiskResult:
    inst = decision.instrument
    cur = position.quantity if position else 0.0
    close_order = (
        OrderRequest(instrument=inst, side=Side.SELL if cur > 0 else Side.BUY, quantity=abs(cur), reduce_only=True)
        if cur
        else None
    )

    if decision.action == Action.HOLD:
        return RiskResult(approved=False, reasons=["觀望"])

    if decision.action == Action.CLOSE:
        if not close_order:
            return RiskResult(approved=False, reasons=["沒有持倉可平"])
        if decision.close_pct < 100:
            qty = round_qty(inst, abs(cur) * decision.close_pct / 100)
            if qty <= 0:
                return RiskResult(approved=False, reasons=["減倉數量低於最小下單單位"])
            close_order.quantity = qty
        return RiskResult(approved=True, orders=[close_order], adjusted=decision)

    # ---------- 以下為開倉檢查 ----------
    reasons: list[str] = []
    want_long = decision.action == Action.OPEN_LONG
    state.roll_day(equity, now)

    if not market_open:
        reasons.append("非交易時段")
    if not want_long and not capabilities.supports_short:
        reasons.append("此市場不支援放空")
    if not want_long and config.long_only:
        reasons.append("此組合設定只做多")
    held = [p for p in all_positions if p.quantity and p.instrument != inst]
    if config.max_positions and cur == 0 and len(held) >= config.max_positions:
        reasons.append(f"已持有 {len(held)} 個標的，達到組合上限 {config.max_positions}")
    if (want_long and cur > 0) or (not want_long and cur < 0):
        if not config.allow_pyramiding:
            return RiskResult(approved=False, reasons=["已有同方向持倉，不加碼"])
    if decision.confidence < config.min_confidence:
        reasons.append(f"信心 {decision.confidence:.2f} 低於門檻 {config.min_confidence}")
    if state.day_start_equity and config.daily_loss_limit_pct > 0:
        loss_pct = (state.day_start_equity - equity) / state.day_start_equity * 100
        if loss_pct >= config.daily_loss_limit_pct:
            state.halted_reason = f"當日虧損 {loss_pct:.2f}% 已達熔斷 {config.daily_loss_limit_pct}%"
            reasons.append(state.halted_reason)
    if state.orders_last_hour(now) >= config.max_orders_per_hour:
        reasons.append(f"近一小時下單已達上限 {config.max_orders_per_hour}")

    d = decision.model_copy()
    notes: list[str] = []
    lev_cap = min(config.max_leverage, capabilities.max_leverage)
    if d.leverage > lev_cap:
        notes.append(f"槓桿 {d.leverage}→{lev_cap}")
        d.leverage = lev_cap
    if d.quantity and price > 0 and equity > 0:
        # 固定數量：換算成佔權益 %（未含槓桿），一樣受單筆倉位上限限制
        implied = d.quantity * price / (equity * d.leverage) * 100
        if implied > config.max_position_pct:
            notes.append(f"數量 {d.quantity:g}→{d.quantity * config.max_position_pct / implied:g}（單筆倉位上限 {config.max_position_pct}%）")
            d.quantity = d.quantity * config.max_position_pct / implied
        d.size_pct = min(100.0, round(min(implied, config.max_position_pct), 4))
    elif d.size_pct > config.max_position_pct:
        notes.append(f"倉位 {d.size_pct}%→{config.max_position_pct}%")
        d.size_pct = config.max_position_pct
    if d.size_pct <= 0 and not d.quantity:
        reasons.append("倉位為 0")

    # 止損
    if d.stop_loss is None and config.require_stop_loss:
        if config.default_stop_loss_pct:
            k = config.default_stop_loss_pct / 100
            d.stop_loss = price * (1 - k) if want_long else price * (1 + k)
            notes.append(f"自動補止損 {d.stop_loss:.6g}")
        else:
            reasons.append("未設定止損")
    if d.stop_loss is not None and ((want_long and d.stop_loss >= price) or (not want_long and d.stop_loss <= price)):
        reasons.append(f"止損價 {d.stop_loss} 方向錯誤（現價 {price}）")
    if d.take_profit is not None and ((want_long and d.take_profit <= price) or (not want_long and d.take_profit >= price)):
        notes.append("止盈價方向錯誤，已忽略")
        d.take_profit = None

    # 數量與總曝險
    if d.quantity:
        qty = round_qty(inst, d.quantity)
    else:
        notional = equity * d.size_pct / 100 * d.leverage
        qty = round_qty(inst, notional / price) if price > 0 else 0.0
    if qty <= 0 and not reasons:
        reasons.append("數量低於最小下單單位")
    same_dir = (want_long and cur > 0) or (not want_long and cur < 0)
    # 同方向加碼時，原本的部位也算進總曝險；反手時原部位會先平掉
    exposure = sum(abs(p.quantity) * (price if p.instrument == inst else p.entry_price)
                   for p in all_positions if p.instrument != inst or same_dir)
    if (exposure + qty * price) > equity * config.max_total_exposure_pct / 100 + 1e-9:
        reasons.append(f"總曝險將超過 {config.max_total_exposure_pct}% 權益")

    if reasons:
        return RiskResult(approved=False, reasons=reasons + notes, adjusted=d)

    orders: list[OrderRequest] = []
    if close_order and ((want_long and cur < 0) or (not want_long and cur > 0)):
        orders.append(close_order)  # 反手：先平再開
    orders.append(OrderRequest(instrument=inst, side=Side.BUY if want_long else Side.SELL, quantity=qty,
                               leverage=d.leverage if capabilities.max_leverage > 1 else None))
    return RiskResult(approved=True, orders=orders, reasons=notes, adjusted=d)
