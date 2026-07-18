import os

from .base import LLMProvider


class GeminiProvider(LLMProvider):
    """Modelli Google Gemini via SDK google-genai (gemini-2.5-flash, ...)."""

    def __init__(self, model_name: str = "gemini-2.5-flash",
                 temperature: float = 0.0, api_key: str | None = None):
        super().__init__(model_name, temperature)
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        from google import genai  # import lazy
        from google.genai import types

        client = genai.Client(api_key=self.api_key) if self.api_key else genai.Client()

        response = client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
            ),
        )
        return response.text or ""
