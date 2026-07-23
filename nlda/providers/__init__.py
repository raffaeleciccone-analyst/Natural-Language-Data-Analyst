"""
Factory dei provider LLM.

Per aggiungere un nuovo provider (es. Google Gemini, Groq, Mistral):
1. crea `nlda/providers/<nome>_provider.py` con una sottoclasse di LLMProvider
2. importala qui e aggiungi una riga in _PROVIDERS e DEFAULT_MODELS
Nient'altro nel resto del codice deve cambiare.
"""
from collections.abc import Callable

from .anthropic_provider import AnthropicProvider
from .base import LLMProvider
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider

# Modello di default suggerito per ciascun provider
DEFAULT_MODELS = {
    "ollama": "qwen2.5:3b",
    "anthropic": "claude-haiku-4-5",   # il modello economico: il compito e generare 5 righe di Pandas
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
}

# Registro nome -> factory. Il tipo è Callable, non type[LLMProvider]: qui i valori
# vengono solo CHIAMATI per costruire un provider, mai usati come classi. Così mypy
# li vede come fabbriche concrete e non lamenta l'istanziazione di una classe astratta
# (LLMProvider lo è) — niente più `type: ignore[abstract]` in get_provider.
_PROVIDERS: dict[str, Callable[..., LLMProvider]] = {
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
}

# Provider che richiedono una API key (gli altri girano in locale). NON serve più a
# get_provider — che passa sempre api_key e lascia decidere a LLMProvider.__init__ —
# ma alla UI, per mostrare il campo "API Key" solo dove ha senso (main.py).
REQUIRES_API_KEY = {"anthropic", "openai", "gemini", "groq"}


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

    # api_key=None è il caso normale dei provider locali (Ollama): LLMProvider.__init__
    # lo accetta e, per i provider cloud, ripiega sulla variabile d'ambiente. Non serve
    # più distinguere chi richiede la chiave: un'unica chiamata copre entrambi i casi.
    return cls(model_name=model, temperature=temperature, api_key=api_key)
