"""大師組合：每位大師用他擅長的方式，各自管理一個獨立的量化組合。

1. AI 讀大師的交易思維檔案 → 設計組合計畫（K 線週期、持倉時間、選股規則、持倉數、倉位、槓桿、能否做空、交易偏好）
2. 使用者確認 / 修改計畫 → 建立一個「AI 交易員」Bot（大腦＝這位大師），各自有獨立的模擬資金
3. 大師組合頁比較各組合的績效
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from goldhunter.ai.base import AIProvider

TIMEFRAMES = ["15m", "1h", "4h", "1d"]


class UniversePlan(BaseModel):
    mode: str = "rules"  # rules / list
    top_n: int = Field(default=10, ge=1, le=50)
    exclude_meme: bool = True
    include_only: list[str] = []
    symbols: list[str] = []  # mode=list 時使用，例如 ["BTC", "ETH"]


class PortfolioPlan(BaseModel):
    suitable: bool = True  # 這位大師適不適合加密貨幣永續合約
    style_summary: str = ""  # 一句話說明這個組合的風格
    reason: str = ""  # 為什麼這樣設計（引用大師的心智模型 / 決策規則）
    timeframe: str = "4h"
    holding_period: str = ""  # 例如「數天到數週」
    universe: UniversePlan = UniversePlan()
    max_positions: int = Field(default=3, ge=1, le=20)
    position_pct: float = Field(default=10, ge=1, le=50)  # 單一標的佔組合資金 %
    max_leverage: int = Field(default=2, ge=1, le=10)
    allow_short: bool = True
    entry_mode: str = "market"  # market / smart（依進場分析掛單等待）
    instructions: str = ""  # 給 AI 交易員的交易偏好（依大師風格）


PLAN_SYSTEM = """你是量化組合設計師。根據一位投資大師的交易思維檔案，替他在「加密貨幣 USDT 永續合約」市場設計一個
最能發揮他擅長方式的獨立量化組合。這個組合由 AI 交易員以他的思維管理。
設計原則：
- 忠於他的風格：順勢 / 逆勢、持倉時間、集中或分散、槓桿態度、能不能做空，都要從檔案推導，不要套用一般做法。
- K 線週期只能是 15m、1h、4h、1d；持倉越久用越大的週期。
- 選股：rules＝依 24 小時成交量前 N 名自動挑選（可排除迷因幣、可只在指定幣種中挑）；list＝固定幾個幣。
- 槓桿 1～10，單一標的佔資金 1～50%，最多同時持有 1～20 個標的。反對槓桿的人一律 1 倍。
- 如果他明確反對加密貨幣或槓桿交易（例如只做長期價值投資），suitable=false，說明原因，
  並給一個最保守的版本（1 倍、只做多、日線、只做 BTC/ETH、低倉位），讓使用者自己決定要不要用。
- instructions 用繁體中文寫給 AI 交易員的具體交易偏好（進出場條件、止損方式、什麼情況不交易），150 字內。
- style_summary、reason、holding_period 用繁體中文。只輸出符合 schema 的 JSON。"""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "suitable": {"type": "boolean"},
        "style_summary": {"type": "string"},
        "reason": {"type": "string"},
        "timeframe": {"type": "string", "enum": TIMEFRAMES},
        "holding_period": {"type": "string"},
        "universe": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["rules", "list"]},
                "top_n": {"type": "integer"},
                "exclude_meme": {"type": "boolean"},
                "include_only": {"type": "array", "items": {"type": "string"}},
                "symbols": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["mode", "top_n", "exclude_meme", "include_only", "symbols"],
            "additionalProperties": False,
        },
        "max_positions": {"type": "integer"},
        "position_pct": {"type": "number"},
        "max_leverage": {"type": "integer"},
        "allow_short": {"type": "boolean"},
        "entry_mode": {"type": "string", "enum": ["market", "smart"]},
        "instructions": {"type": "string"},
    },
    "required": ["suitable", "style_summary", "reason", "timeframe", "holding_period", "universe", "max_positions",
                 "position_pct", "max_leverage", "allow_short", "entry_mode", "instructions"],
    "additionalProperties": False,
}


def clamp_plan(raw: dict) -> PortfolioPlan:
    """AI 的輸出一律經過範圍檢查"""
    u = raw.get("universe") or {}
    tf = raw.get("timeframe") if raw.get("timeframe") in TIMEFRAMES else "4h"
    symbols = [s.upper().replace("/USDT", "").strip() for s in (u.get("symbols") or []) if s][:20]
    plan = PortfolioPlan(
        suitable=bool(raw.get("suitable", True)),
        style_summary=str(raw.get("style_summary", ""))[:200],
        reason=str(raw.get("reason", ""))[:800],
        timeframe=tf,
        holding_period=str(raw.get("holding_period", ""))[:50],
        universe=UniversePlan(
            mode="list" if u.get("mode") == "list" and symbols else "rules",
            top_n=min(max(int(u.get("top_n") or 10), 1), 50),
            exclude_meme=bool(u.get("exclude_meme", True)),
            include_only=[s.upper() for s in (u.get("include_only") or [])][:30],
            symbols=symbols,
        ),
        max_positions=min(max(int(raw.get("max_positions") or 3), 1), 20),
        position_pct=min(max(float(raw.get("position_pct") or 10), 1), 50),
        max_leverage=min(max(int(raw.get("max_leverage") or 1), 1), 10),
        allow_short=bool(raw.get("allow_short", True)),
        entry_mode="smart" if raw.get("entry_mode") == "smart" else "market",
        instructions=str(raw.get("instructions", ""))[:600],
    )
    return plan


async def design_plan(ai: AIProvider, name: str, profile: str) -> PortfolioPlan:
    system = f"{PLAN_SYSTEM}\n\n## {name} 的交易思維檔案\n{profile}"
    r = await ai.complete_json(system, f"請替 {name} 設計他的加密貨幣永續合約量化組合。", PLAN_SCHEMA)
    return clamp_plan(r.decision)


def plan_to_bot_fields(plan: PortfolioPlan) -> dict:
    """計畫 → Bot 設定（標的範圍、週期、風控、進場方式、AI 交易員偏好）"""
    universe = ({"mode": "rules", "top_n": plan.universe.top_n, "exclude_meme": plan.universe.exclude_meme,
                 "include_only": plan.universe.include_only}
                if plan.universe.mode == "rules" else {"mode": "list"})
    symbols = [f"crypto:{s}/USDT:perp" for s in plan.universe.symbols] if plan.universe.mode == "list" else []
    return {
        "timeframe": plan.timeframe,
        "interval_sec": {"15m": 60, "1h": 120, "4h": 300, "1d": 600}.get(plan.timeframe, 120),
        "universe": universe,
        "symbols": symbols,
        "risk": {
            "max_position_pct": plan.position_pct,
            "max_leverage": plan.max_leverage,
            "max_positions": plan.max_positions,
            "long_only": not plan.allow_short,
            "max_total_exposure_pct": min(1000.0, plan.position_pct * plan.max_positions * plan.max_leverage),
        },
        "entry": {"mode": plan.entry_mode},
        "instructions": plan.instructions,
    }
