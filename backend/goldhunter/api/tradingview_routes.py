"""TradingView Alert Webhook 接收端（不使用 Bearer token，改用每個 Bot 的 webhook 密語驗證）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlmodel import Session

from goldhunter.core.models import Instrument
from goldhunter.engine.manager import manager
from goldhunter.store.db import Bot, get_engine
from goldhunter.tradingview.webhook import TradingViewAlert, alert_to_decision, check_passphrase

router = APIRouter()


@router.post("/tradingview/webhook/{bot_id}")
async def tradingview_webhook(bot_id: int, alert: TradingViewAlert):
    with Session(get_engine()) as s:
        bot = s.get(Bot, bot_id)
        if not bot or not check_passphrase(alert, bot.webhook_secret):
            # 不透露 bot 是否存在
            raise HTTPException(401, "驗證失敗")
        symbols = list(bot.symbols)
    try:
        runner = await manager.get_or_start_for_signal(bot_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e

    if alert.symbol:
        inst = Instrument.parse(alert.symbol) if alert.symbol.count(":") == 2 else next(
            (i for i in runner.instruments if i.symbol.replace("/", "") == alert.symbol.replace("/", "").upper()),
            None)
        if inst is None or inst not in runner.instruments:
            raise HTTPException(400, f"交易對 {alert.symbol} 不在此 Bot 的清單中")
    else:
        inst = Instrument.parse(symbols[0])

    pos = next((p for p in await runner.exchange.fetch_positions() if p.instrument == inst), None)
    try:
        decision = alert_to_decision(alert, inst, pos.quantity if pos else 0.0,
                                     default_size_pct=min(10.0, runner.risk.max_position_pct))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    entry = await runner.handle_decision(decision)
    return {"approved": entry.approved, "reasons": entry.reasons, "decision_id": entry.id}
