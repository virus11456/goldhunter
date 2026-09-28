from __future__ import annotations

from goldhunter.ai.anthropic_provider import DEFAULT_MODEL, AnthropicProvider
from goldhunter.ai.base import AIProvider
from goldhunter.ai.openai_compat import PRESETS, OpenAICompatProvider


def available_providers() -> list[dict]:
    items = [{"id": "anthropic", "label": "Anthropic Claude", "default_model": DEFAULT_MODEL, "needs_key": True}]
    labels = {"openai": "OpenAI", "deepseek": "DeepSeek", "qwen": "通義千問", "ollama": "Ollama（本地）"}
    for k, v in PRESETS.items():
        items.append({"id": k, "label": labels[k], "default_model": v["model"], "needs_key": k != "ollama",
                      "default_base_url": v["base_url"]})
    return items


def build_provider(provider: str, model: str = "", api_key: str | None = None,
                   base_url: str | None = None, **options) -> AIProvider:
    if provider == "anthropic":
        return AnthropicProvider(model=model, api_key=api_key, base_url=base_url, **options)
    if provider in PRESETS:
        return OpenAICompatProvider(model=model, api_key=api_key, base_url=base_url, preset=provider, **options)
    raise ValueError(f"未知的 AI 供應商：{provider}")
