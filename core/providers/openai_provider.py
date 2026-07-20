from typing import Any

from core.config import settings

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """Modelli OpenAI (gpt-4o, gpt-4o-mini, ...)."""

    ENV_VAR = "OPENAI_API_KEY"
    # base_url alternativa (per API compatibili OpenAI); None = endpoint OpenAI.
    base_url: str | None = None

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        from openai import OpenAI  # import lazy

        # dict[str, Any]: sono kwargs eterogenei per il costruttore OpenAI; con
        # 'object' mypy rifiuterebbe lo splat verso ogni parametro tipizzato.
        kwargs: dict[str, Any] = {"timeout": settings.request_timeout}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = OpenAI(**kwargs)

        response = client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
