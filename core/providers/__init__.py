"""
Factory dei provider LLM.

Per aggiungere un nuovo provider (es. Google Gemini, Groq, Mistral):
1. crea `core/providers/<nome>_provider.py` con una sottoclasse di LLMProvider
2. importala qui e aggiungi una riga in _PROVIDERS e DEFAULT_MODELS
Nient'altro nel resto del codice deve cambiare.
"""
from .base import LLMProvider
from .ollama_provider import OllamaProvider
from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAIProvider
from .gemini_provider import GeminiProvider

# Modello di default suggerito per ciascun provider
DEFAULT_MODELS = {
    "ollama": "qwen2.5:3b",
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
}

# Registro nome -> classe
_PROVIDERS = {
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}

# Quali provider richiedono una API key (gli altri girano in locale)
REQUIRES_API_KEY = {"anthropic", "openai", "gemini"}


def available_providers() -> list[str]:
    return list(_PROVIDERS.keys())


def get_provider(name: str, model_name: str | None = None,
                 temperature: float = 0.0, api_key: str | None = None) -> LLMProvider:
    key = (name or "").lower()
    if key not in _PROVIDERS:
        raise ValueError(
            f"Provider '{name}' non supportato. Disponibili: {available_providers()}"
        )

    cls = _PROVIDERS[key]
    model = model_name or DEFAULT_MODELS[key]

    if key in REQUIRES_API_KEY:
        return cls(model_name=model, temperature=temperature, api_key=api_key)
    return cls(model_name=model, temperature=temperature)
