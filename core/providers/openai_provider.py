from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """Modelli OpenAI (gpt-4o, gpt-4o-mini, ...)."""

    ENV_VAR = "OPENAI_API_KEY"
    # base_url alternativa (per API compatibili OpenAI); None = endpoint OpenAI.
    base_url: str | None = None

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        from openai import OpenAI  # import lazy

        kwargs = {}
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
