"""AI 模型統一介面：輸入行情脈絡，輸出結構化交易決策 JSON。

AI 永遠只「建議」，決策一定要經過風控（risk.manager）才會下單。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from pydantic import BaseModel

# 模型必須回傳的 JSON 結構（同時用於 Claude structured outputs 與 OpenAI json 模式驗證）
DECISION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["open_long", "open_short", "close", "hold"]},
        "size_pct": {"type": "number", "description": "佔帳戶權益百分比 0-100"},
        "leverage": {"type": "integer"},
        "stop_loss": {"type": ["number", "null"]},
        "take_profit": {"type": ["number", "null"]},
        "confidence": {"type": "number", "description": "0-1"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "size_pct", "leverage", "stop_loss", "take_profit", "confidence", "reasoning"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是一位紀律嚴謹的量化交易分析師。根據使用者提供的行情資料、技術指標、目前持倉與策略指示，
輸出單一交易決策。規則：
- 只能輸出符合 schema 的 JSON。
- 沒有明確優勢時輸出 action="hold"。
- 開倉必須給 stop_loss；take_profit 建議給出。
- 若市場不支援放空，不可輸出 open_short。
- size_pct 與 leverage 不得超過使用者給的上限。
- reasoning 用繁體中文簡述理由（100 字內）。"""


class AIResult(BaseModel):
    decision: dict
    raw_text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class AIProviderError(RuntimeError):
    pass


class AIProvider(ABC):
    provider: str = "base"

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, **options):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.options = options

    @abstractmethod
    async def complete_json(self, system: str, user: str, schema: dict) -> AIResult:
        """要求模型回傳符合 schema 的 JSON。"""

    @abstractmethod
    async def complete_text(self, system: str, user: str, max_tokens: int = 16000) -> str:
        """一般文字輸出（例如 Pine Script 轉 Python）。"""

    async def decide(self, user_prompt: str) -> AIResult:
        return await self.complete_json(SYSTEM_PROMPT, user_prompt, DECISION_SCHEMA)


def parse_json_text(text: str) -> dict:
    """容錯解析：去除 ```json 圍欄後解析"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise AIProviderError(f"模型沒有回傳 JSON：{text[:200]}")
    return json.loads(t[start : end + 1])
