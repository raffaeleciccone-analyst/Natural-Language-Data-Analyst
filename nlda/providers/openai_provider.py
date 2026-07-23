from collections.abc import Iterator
from typing import Any

from nlda.config import settings
from nlda.pricing import Usage

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """Modelli OpenAI (gpt-4o, gpt-4o-mini, ...)."""

    ENV_VAR = "OPENAI_API_KEY"
    # base_url alternativa (per API compatibili OpenAI); None = endpoint OpenAI.
    base_url: str | None = None

    def _client(self):
        from openai import OpenAI  # import lazy

        # dict[str, Any]: sono kwargs eterogenei per il costruttore OpenAI; con
        # 'object' mypy rifiuterebbe lo splat verso ogni parametro tipizzato.
        kwargs: dict[str, Any] = {"timeout": settings.request_timeout}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)

    def _messages(self, system_prompt: str, user_prompt: str) -> list[dict]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client().chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=self._messages(system_prompt, user_prompt),
        )
        # Token consumati, input e output separati (getattr difensivo: una risposta
        # senza usage — o un finto nei test — lascia semplicemente None).
        usage = getattr(response, "usage", None)
        self._last_usage = Usage(getattr(usage, "prompt_tokens", None),
                                 getattr(usage, "completion_tokens", None))
        return response.choices[0].message.content or ""

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        self._last_usage = Usage()
        response = self._client().chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=self._messages(system_prompt, user_prompt),
            stream=True,
            stream_options={"include_usage": True},  # l'usage arriva nell'ultimo chunk
        )
        for chunk in response:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                self._last_usage = Usage(getattr(usage, "prompt_tokens", None),
                                         getattr(usage, "completion_tokens", None))
            # l'ultimo chunk (quello con l'usage) ha choices vuoto: si salta
            if getattr(chunk, "choices", None):
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
