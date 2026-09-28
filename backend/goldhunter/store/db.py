"""SQLite 資料庫（SQLModel）。API Key 一律以 *_enc 欄位加密存放。"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, Session, SQLModel, create_engine

from goldhunter.config import get_settings


def _now() -> datetime:
    return datetime.now(UTC)


class ExchangeAccount(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    exchange_id: str  # binance / okx / bybit / hyperliquid
    api_key_enc: str | None = None
    secret_enc: str | None = None
    passphrase_enc: str | None = None
    testnet: bool = False
    paper: bool = True  # 預設模擬交易
    paper_cash: float = 10_000.0
    created_at: datetime = Field(default_factory=_now)


class AIModelConfig(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    provider: str  # anthropic / openai / deepseek / qwen / ollama
    model: str = ""
    api_key_enc: str | None = None
    base_url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class StrategyConfig(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str  # ma_cross / rsi_reversion / ai / python / tradingview
    params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    code: str | None = Field(default=None, sa_column=Column(Text))
    pine_source: str | None = Field(default=None, sa_column=Column(Text))
    # pending_review：待審核，不能交易
    # paper_only：自動審核通過，模擬期間只能用在模擬帳戶；滿 paper_days 天且模擬成交達 min_paper_trades 筆後自動開放實盤
    # active：可用於所有帳戶
    status: str = "active"
    review: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # 最近一次自動審查報告
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # 最近一次回測績效
    approved_at: datetime | None = None  # 審查通過（進入模擬期）的時間
    paper_days: int = 7
    min_paper_trades: int = 3
    created_at: datetime = Field(default_factory=_now)


class Bot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    account_id: int = Field(foreign_key="exchangeaccount.id")
    strategy_id: int = Field(foreign_key="strategyconfig.id")
    ai_model_id: int | None = Field(default=None, foreign_key="aimodelconfig.id")
    symbols: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # Instrument 字串
    timeframe: str = "15m"
    interval_sec: int = 60
    risk: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # AI 副駕駛設定（訊號審核 / 持倉管理 / 參數微調），見 copilot.config.CopilotConfig
    copilot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # 此 Bot 專屬的策略參數覆寫（AI 參數微調套用在這裡，不影響其他 Bot）
    params_override: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "stopped"  # stopped / running / error
    # TradingView Webhook 驗證密語（每個 Bot 各自一組）
    webhook_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    last_error: str | None = None
    last_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


class DecisionLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    ts: datetime = Field(default_factory=_now, index=True)
    instrument: str
    source: str
    action: str
    decision: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    approved: bool = False
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    ai_model: str | None = None
    ai_raw: str | None = Field(default=None, sa_column=Column(Text))
    input_tokens: int = 0
    output_tokens: int = 0


class Trade(SQLModel, table=True):
    """每一筆成交紀錄"""

    id: int | None = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    decision_id: int | None = None
    ts: datetime = Field(default_factory=_now, index=True)
    instrument: str
    side: str
    quantity: float
    price: float | None
    fee: float = 0.0
    reduce_only: bool = False
    realized_pnl: float | None = None
    order_id: str | None = None
    status: str = "filled"
    source: str = "strategy"
    note: str | None = None


class EquitySnapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    ts: datetime = Field(default_factory=_now, index=True)
    equity: float
    # 對照組：同一策略「不經 AI 副駕駛」的模擬權益，用來比較 AI 有沒有幫上忙
    baseline_equity: float | None = None


class TuningRun(SQLModel, table=True):
    """AI 參數微調紀錄"""

    id: int | None = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    ts: datetime = Field(default_factory=_now, index=True)
    current_params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    proposed_params: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    current_metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    proposed_metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    reasoning: str = Field(default="", sa_column=Column(Text))
    status: str = "proposed"  # proposed / applied / rejected / failed
    note: str | None = None


class AppSetting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class BacktestRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    strategy_id: int | None = None
    strategy_name: str
    instrument: str
    timeframe: str
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    equity_curve: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    trades: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


_engine = None


def _add_missing_columns(engine) -> None:
    """簡易遷移：新版本新增的欄位自動 ALTER TABLE 補上（SQLite 的 create_all 不會改既有資料表）"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = col.type.compile(engine.dialect)
                default = ""
                if col.default is not None and getattr(col.default, "is_scalar", False):
                    v = col.default.arg
                    default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v)}"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{default}'))


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().db_url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
        _add_missing_columns(_engine)
    return _engine


def reset_engine(url: str | None = None):
    """測試用"""
    global _engine
    _engine = create_engine(url or get_settings().db_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(_engine)
    return _engine


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
