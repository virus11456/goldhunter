"""OpenAI 相容介面：OpenAI、DeepSeek、通義千問、Ollama / LM Studio 本地模型等。"""

from __future__ import annotations

import json

import openai

from goldhunter.ai.base import AIProvider, AIProviderError, AIResult, parse_json_text

PRESETS = {
    "openai": {"base_url": None, "model": "gpt-5"},
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-max"},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.1"},
}


class OpenAICompatProvider(AIProvider):
    def __init__(self, model: str = "", api_key: str | None = None, base_url: str | None = None,
                 preset: str = "openai", **options):
        p = PRESETS.get(preset, PRESETS["openai"])
        super().__init__(model or p["model"], api_key, base_url or p["base_url"], **options)
        self.provider = preset
        self.client = openai.AsyncOpenAI(api_key=api_key or "not-needed", base_url=self.base_url)

    async def _chat(self, system: str, user: str, json_mode: bool):
        kw = {"response_format": {"type": "json_object"}} if json_mode else {}
        try:
            return await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                **kw,
            )
        except openai.AuthenticationError as e:
            raise AIProviderError(f"{self.provider} API Key 無效") from e
        except openai.APIStatusError as e:
            raise AIProviderError(f"{self.provider} API 錯誤 {e.status_code}") from e
        except openai.APIConnectionError as e:
            raise AIProviderError(f"無法連線到 {self.provider}") from e

    async def complete_json(self, system: str, user: str, schema: dict) -> AIResult:
        sys = f"{system}\n\n只輸出 JSON，符合此 JSON Schema：\n{json.dumps(schema, ensure_ascii=False)}"
        resp = await self._chat(sys, user, json_mode=True)
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        return AIResult(
            decision=parse_json_text(text),
            raw_text=text,
            model=resp.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    async def complete_text(self, system: str, user: str, max_tokens: int = 16000) -> str:
        resp = await self._chat(system, user, json_mode=False)
        return resp.choices[0].message.content or ""
