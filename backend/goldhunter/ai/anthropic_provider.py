"""Claude（Anthropic 官方 SDK）。

- 使用 structured outputs（output_config.format）保證回傳合法 JSON
- 啟用伺服器端 refusal fallback：若模型拒答，API 會自動改由備援模型完成
"""

from __future__ import annotations

import json

import anthropic

from goldhunter.ai.base import AIProvider, AIProviderError, AIResult

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(AIProvider):
    provider = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, base_url: str | None = None, **options):
        super().__init__(model or DEFAULT_MODEL, api_key, base_url, **options)
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url or None)
        # effort: low / medium / high / xhigh / max；留空＝模型預設
        self.effort: str | None = options.get("effort") or None
        self.use_fallbacks: bool = options.get("fallbacks", True)

    def _kwargs(self) -> dict:
        kw: dict = {"model": self.model}
        output_config: dict = {}
        if self.effort:
            output_config["effort"] = self.effort
        if self.use_fallbacks:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw | ({"output_config": output_config} if output_config else {})

    async def _create(self, **kw):
        base = self._kwargs()
        if "output_config" in kw and "output_config" in base:
            kw["output_config"] = base.pop("output_config") | kw["output_config"]
        try:
            if "betas" in base:
                async with self.client.beta.messages.stream(**base, **kw) as stream:
                    msg = await stream.get_final_message()
            else:
                async with self.client.messages.stream(**base, **kw) as stream:
                    msg = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AIProviderError("Anthropic API Key 無效") from e
        except anthropic.RateLimitError as e:
            raise AIProviderError("Anthropic 請求過於頻繁（429），請稍後再試") from e
        except anthropic.APIStatusError as e:
            raise AIProviderError(f"Anthropic API 錯誤 {e.status_code}：{e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIProviderError("無法連線到 Anthropic API") from e
        if msg.stop_reason == "refusal":
            raise AIProviderError("模型拒絕回應此請求")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return msg, text

    async def complete_json(self, system: str, user: str, schema: dict) -> AIResult:
        msg, text = await self._create(
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if msg.stop_reason == "max_tokens":
            raise AIProviderError("模型輸出被截斷（max_tokens）")
        return AIResult(
            decision=json.loads(text),
            raw_text=text,
            model=msg.model,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )

    async def complete_text(self, system: str, user: str, max_tokens: int = 32000) -> str:
        _, text = await self._create(
            max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}]
        )
        return text
