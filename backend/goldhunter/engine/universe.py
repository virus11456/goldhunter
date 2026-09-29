"""標的範圍：手動清單，或依規則（24 小時成交量前 N 名、排除迷因幣 / 指定幣種、只做指定幣種）自動挑選。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from goldhunter.core.models import Instrument, InstrumentType, Market

# 常見迷因幣（可在規則中排除）
MEME_COINS = {
    "DOGE", "SHIB", "PEPE", "1000PEPE", "kPEPE", "WIF", "BONK", "1000BONK", "kBONK", "FLOKI", "1000FLOKI",
    "BOME", "MEME", "POPCAT", "MEW", "BRETT", "TURBO", "NEIRO", "PNUT", "GOAT", "MOODENG", "FARTCOIN", "TRUMP",
    "PENGU", "SPX", "MOG", "1000SATS", "PEOPLE", "DOGS", "HMSTR", "ACT", "CHILLGUY", "1000CAT", "kSHIB",
}


class UniverseRules(BaseModel):
    mode: str = "list"  # list / rules
    top_n: int = Field(default=10, ge=1, le=100)
    exclude_meme: bool = True
    exclude: list[str] = []  # 例如 ["LUNA", "FTT"]
    include_only: list[str] = []  # 有填就只在這些幣裡挑
    refresh_hours: int = Field(default=6, ge=1, le=168)


def pick(volumes: list[tuple[str, float]], rules: UniverseRules) -> list[str]:
    """volumes：[(幣種, 24 小時成交額 USDT)]，回傳挑中的幣種（依成交額排序）"""
    excl = {c.upper() for c in rules.exclude}
    only = {c.upper() for c in rules.include_only}
    meme = {m.upper() for m in MEME_COINS}
    out = []
    for base, _vol in sorted(volumes, key=lambda x: x[1], reverse=True):
        b = base.upper()
        if b in excl or (rules.exclude_meme and b in meme) or (only and b not in only):
            continue
        out.append(base)
        if len(out) >= rules.top_n:
            break
    return out


def to_instruments(bases: list[str]) -> list[Instrument]:
    return [Instrument(market=Market.CRYPTO, symbol=f"{b}/USDT", type=InstrumentType.PERP) for b in bases]
