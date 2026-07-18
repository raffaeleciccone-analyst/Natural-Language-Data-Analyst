import os

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """Modelli OpenAI (gpt-4o, gpt-4o-mini, ...)."""

    def __init__(self, model_name: str = "gpt-4o-mini",
                 temperature: float = 0.0, api_key: str | None = None):
        super().__init__(model_name, temperature)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        from openai import OpenAI  # import lazy

        client = OpenAI(api_key=self.api_key) if self.api_key else OpenAI()

        response = client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
