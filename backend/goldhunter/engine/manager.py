"""管理所有 Bot 的生命週期（啟動、停止、緊急全停），並從資料庫組裝 BotRunner。"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from goldhunter.ai.base import AIProvider
from goldhunter.ai.registry import build_provider
from goldhunter.copilot.config import CopilotConfig
from goldhunter.core.models import Instrument
from goldhunter.core.secrets import decrypt
from goldhunter.engine.bot import BotRunner
from goldhunter.exchanges.base import ExchangeAdapter
from goldhunter.exchanges.registry import build_exchange
from goldhunter.personas.attach import attach_personas
from goldhunter.risk.manager import RiskConfig
from goldhunter.store.db import AIModelConfig, Bot, ExchangeAccount, StrategyConfig, get_engine
from goldhunter.strategies.lifecycle import check_usable
from goldhunter.strategies.registry import attach_reference, build_strategy

log = logging.getLogger("goldhunter.manager")


def exchange_from_account(acc: ExchangeAccount) -> ExchangeAdapter:
    return build_exchange(
        acc.exchange_id, api_key=decrypt(acc.api_key_enc), secret=decrypt(acc.secret_enc),
        passphrase=decrypt(acc.passphrase_enc), testnet=acc.testnet, paper=acc.paper, paper_cash=acc.paper_cash,
    )


def provider_from_config(cfg: AIModelConfig) -> AIProvider:
    return build_provider(cfg.provider, cfg.model, decrypt(cfg.api_key_enc), cfg.base_url, **(cfg.options or {}))


class BotManager:
    def __init__(self):
        self.runners: dict[int, BotRunner] = {}

    def build_runner(self, session: Session, bot: Bot) -> BotRunner:
        acc = session.get(ExchangeAccount, bot.account_id)
        strat_cfg = session.get(StrategyConfig, bot.strategy_id)
        if not acc or not strat_cfg:
            raise ValueError("Bot 的交易所帳戶或策略不存在")
        check_usable(strat_cfg, acc.paper, session)
        ai = None
        if bot.ai_model_id:
            ai_cfg = session.get(AIModelConfig, bot.ai_model_id)
            if not ai_cfg:
                raise ValueError("AI 模型設定不存在")
            ai = provider_from_config(ai_cfg)
        strategy = None
        if strat_cfg.kind != "tradingview":
            params = {**(strat_cfg.params or {}), **(bot.params_override or {})}
            strategy = build_strategy(strat_cfg.kind, params, strat_cfg.code, ai=ai)
            attach_reference(strategy, session, paper_account=acc.paper)
            attach_personas(strategy, session, paper_account=acc.paper)
            if strategy.uses_ai and ai is None:
                raise ValueError("此策略需要 AI 模型，請在 Bot 設定中選擇")
        if not bot.symbols and (bot.universe or {}).get("mode") != "rules":
            raise ValueError("Bot 至少需要一個交易對")
        return BotRunner(
            bot_id=bot.id, name=bot.name, exchange=exchange_from_account(acc), strategy=strategy,  # type: ignore[arg-type]
            instruments=[Instrument.parse(s) for s in bot.symbols], timeframe=bot.timeframe,
            interval_sec=bot.interval_sec, risk=RiskConfig(**(bot.risk or {})), ai=ai,
            copilot=CopilotConfig(**(bot.copilot or {})), exchange_id=acc.exchange_id, strategy_id=strat_cfg.id,
            universe=bot.universe or {},
        )

    async def start(self, bot_id: int) -> BotRunner:
        if (r := self.runners.get(bot_id)) and r.running:
            return r
        with Session(get_engine()) as s:
            bot = s.get(Bot, bot_id)
            if not bot:
                raise ValueError("Bot 不存在")
            runner = self.build_runner(s, bot)
            bot.status, bot.last_error = "running", None
            s.add(bot)
            s.commit()
        self.runners[bot_id] = runner
        runner.start()
        return runner

    async def stop(self, bot_id: int) -> None:
        if runner := self.runners.pop(bot_id, None):
            await runner.stop()
        with Session(get_engine()) as s:
            if bot := s.get(Bot, bot_id):
                bot.status = "stopped"
                s.add(bot)
                s.commit()

    async def stop_all(self) -> list[int]:
        ids = list(self.runners)
        for i in ids:
            await self.stop(i)
        return ids

    async def get_or_start_for_signal(self, bot_id: int) -> BotRunner:
        """TradingView 訊號：只接受已啟動的 Bot（避免停止中的 Bot 被外部訊號觸發）"""
        runner = self.runners.get(bot_id)
        if not runner or not runner.running:
            raise ValueError("Bot 未啟動，訊號已忽略")
        return runner

    async def resume(self) -> None:
        """服務重啟後恢復原本在跑的 Bot"""
        with Session(get_engine()) as s:
            ids = [b.id for b in s.exec(select(Bot).where(Bot.status == "running"))]
        for i in ids:
            try:
                await self.start(i)  # type: ignore[arg-type]
            except Exception as e:
                log.error("恢復 bot %s 失敗：%s", i, e)
                with Session(get_engine()) as s:
                    if bot := s.get(Bot, i):
                        bot.status, bot.last_error = "error", str(e)
                        s.add(bot)
                        s.commit()


manager = BotManager()
