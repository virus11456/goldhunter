"""TradingView Alert Webhook → Decision。

在 TradingView 建立 Alert，Webhook URL 填：
  https://你的網域/api/tradingview/webhook/{bot_id}
訊息（Message）填 JSON，例如：
  {"passphrase": "你的密語", "action": "{{strategy.order.action}}", "size_pct": 10,
   "market_position": "{{strategy.market_position}}"}

支援的 action：buy / sell / long / short / close / exit / flat
若同時帶 market_position（long / short / flat），以它為準（最適合 strategy 腳本）。
"""

from __future__ import annotations

import hmac

from pydantic import BaseModel, Field

from goldhunter.core.models import Action, Decision, Instrument


class TradingViewAlert(BaseModel):
    passphrase: str = ""
    action: str = ""
    market_position: str | None = None
    symbol: str | None = None  # 可選：覆寫 bot 的交易對（Instrument 格式或 BTC/USDT）
    size_pct: float | None = Field(default=None, ge=0, le=100)
    leverage: int | None = Field(default=None, ge=1, le=125)
    stop_loss: float | None = None
    take_profit: float | None = None
    price: float | None = None
    comment: str | None = None


def check_passphrase(alert: TradingViewAlert, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(alert.passphrase.encode(), expected.encode())


def alert_to_decision(alert: TradingViewAlert, instrument: Instrument, current_position: float,
                      default_size_pct: float, default_leverage: int = 1) -> Decision:
    mp = (alert.market_position or "").lower()
    act = alert.action.lower().strip()
    if mp == "flat" or act in {"close", "exit", "flat"}:
        action = Action.CLOSE
    elif mp == "long" or act in {"buy", "long"}:
        action = Action.OPEN_LONG
    elif mp == "short" or act in {"sell", "short"}:
        # 純 sell（沒有 market_position）且目前持有多單 → 視為平多，而非直接反手做空
        action = Action.CLOSE if act == "sell" and not mp and current_position > 0 else Action.OPEN_SHORT
    else:
        raise ValueError(f"無法辨識的 TradingView action：{alert.action!r}")
    return Decision(
        instrument=instrument,
        action=action,
        size_pct=alert.size_pct if alert.size_pct is not None else default_size_pct,
        leverage=alert.leverage or default_leverage,
        stop_loss=alert.stop_loss,
        take_profit=alert.take_profit,
        reasoning=f"TradingView 訊號：{alert.action or mp}" + (f"（{alert.comment}）" if alert.comment else ""),
        source="tradingview",
    )
