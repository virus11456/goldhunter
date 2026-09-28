"""市場情報中心：整合各來源、快取，並整理成 AI 看得懂的文字。"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlmodel import Session

from goldhunter.core.models import Instrument
from goldhunter.core.secrets import decrypt, encrypt, mask
from goldhunter.intel import sources
from goldhunter.store.db import AppSetting, get_engine

SETTINGS_KEY = "intel"
TTL = {"derivatives": 300, "fear_greed": 3600, "news": 600, "macro": 6 * 3600, "calendar": 3600}


class IntelSettings(BaseModel):
    derivatives: bool = True
    fear_greed: bool = True
    news: bool = True
    macro: bool = True
    calendar: bool = True
    rss_urls: list[str] = sources.DEFAULT_RSS
    fred_api_key_enc: str | None = None
    cryptopanic_token_enc: str | None = None

    def public(self) -> dict:
        d = self.model_dump(exclude={"fred_api_key_enc", "cryptopanic_token_enc"})
        d["fred_api_key"] = mask(decrypt(self.fred_api_key_enc))
        d["cryptopanic_token"] = mask(decrypt(self.cryptopanic_token_enc))
        return d


def load_settings() -> IntelSettings:
    with Session(get_engine()) as s:
        row = s.get(AppSetting, SETTINGS_KEY)
        return IntelSettings(**(row.value if row else {}))


def save_settings(update: dict[str, Any]) -> IntelSettings:
    cur = load_settings()
    data = cur.model_dump()
    for k in ("derivatives", "fear_greed", "news", "macro", "calendar", "rss_urls"):
        if k in update and update[k] is not None:
            data[k] = update[k]
    if update.get("fred_api_key"):
        data["fred_api_key_enc"] = encrypt(update["fred_api_key"])
    if update.get("cryptopanic_token"):
        data["cryptopanic_token_enc"] = encrypt(update["cryptopanic_token"])
    new = IntelSettings(**data)
    with Session(get_engine()) as s:
        row = s.get(AppSetting, SETTINGS_KEY) or AppSetting(key=SETTINGS_KEY, value={})
        row.value = new.model_dump()
        s.add(row)
        s.commit()
    hub.clear()
    return new


class IntelSnapshot(BaseModel):
    instrument: str
    fetched_at: str
    derivatives: dict = {}
    fear_greed: dict | None = None
    news: list[dict] = []
    macro: dict = {}
    events: list[dict] = []  # 未來 48 小時內的高影響事件

    def upcoming_event_within(self, minutes: int, now: datetime | None = None) -> dict | None:
        """前後 minutes 分鐘內是否有高影響經濟事件"""
        t = now or datetime.now(UTC)
        for e in self.events:
            et = datetime.fromisoformat(e["time"])
            if abs((et - t).total_seconds()) <= minutes * 60:
                return e
        return None

    def to_prompt(self) -> str:
        lines = [f"## 市場情報（{self.fetched_at} UTC）"]
        if self.derivatives:
            lines.append(f"合約數據：{self.derivatives}")
        if self.fear_greed:
            fg = self.fear_greed
            lines.append(f"恐懼貪婪指數：{fg['value']}（{fg['label']}），昨日 {fg.get('yesterday')}")
        if self.macro:
            lines.append("總經數據：")
            for v in self.macro.values():
                extra = f"，年增 {v['yoy_pct']}%" if "yoy_pct" in v else ""
                lines.append(f"- {v['label']}：{v['value']}（{v['date']}，前值 {v.get('previous')}{extra}）")
        if self.events:
            lines.append("近期高影響經濟事件（UTC）：")
            lines += [f"- {e['time']} {e['country']} {e['title']}（預期 {e.get('forecast')}，前值 {e.get('previous')}）"
                      for e in self.events[:8]]
        if self.news:
            lines.append("最新新聞標題：")
            lines += [f"- [{(n.get('published') or '')[:16]}] {n['title']}" for n in self.news[:10]]
        if len(lines) == 1:
            lines.append("（目前沒有可用的外部情報）")
        return "\n".join(lines)


class IntelHub:
    def __init__(self):
        self._cache: dict[tuple, tuple[float, Any]] = {}

    def clear(self) -> None:
        self._cache.clear()

    async def _cached(self, key: tuple, ttl: int, fn):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        self._cache[key] = (time.time(), val)
        return val

    async def snapshot(self, exchange_id: str, inst: Instrument, settings: IntelSettings | None = None) -> IntelSnapshot:
        st = settings or load_settings()
        base = inst.symbol.split("/")[0]
        keywords = [base, {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(base, base.lower()), "crypto"]
        tasks: dict[str, Any] = {}
        if st.derivatives:
            tasks["derivatives"] = self._cached(("der", exchange_id, str(inst)), TTL["derivatives"],
                                                lambda: sources.derivatives(exchange_id, inst))
        if st.fear_greed:
            tasks["fear_greed"] = self._cached(("fg",), TTL["fear_greed"], sources.fear_greed)
        if st.news:
            token = decrypt(st.cryptopanic_token_enc)
            tasks["news"] = self._cached(("news", base), TTL["news"],
                                         lambda: sources.news(keywords, st.rss_urls, token))
        if st.macro:
            key = decrypt(st.fred_api_key_enc)
            tasks["macro"] = self._cached(("macro",), TTL["macro"], lambda: sources.macro(key))
        if st.calendar:
            tasks["events"] = self._cached(("cal",), TTL["calendar"], sources.economic_calendar)
        results = dict(zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True)))
        clean = {k: v for k, v in results.items() if not isinstance(v, BaseException) and v is not None}
        now = datetime.now(UTC)
        if "events" in clean:
            clean["events"] = [e for e in clean["events"]
                               if now - timedelta(hours=2) <= datetime.fromisoformat(e["time"]) <= now + timedelta(hours=48)]
        return IntelSnapshot(instrument=str(inst), fetched_at=now.strftime("%Y-%m-%d %H:%M"), **clean)


hub = IntelHub()
