from nlda.config import settings

from .base import LLMProvider


class GeminiProvider(LLMProvider):
    """Modelli Google Gemini via SDK google-genai (gemini-2.0-flash, ...)."""

    ENV_VAR = ("GOOGLE_API_KEY", "GEMINI_API_KEY")

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        from google import genai  # import lazy
        from google.genai import types

        # timeout in millisecondi (convenzione dell'SDK google-genai)
        http_options = types.HttpOptions(timeout=int(settings.request_timeout * 1000))
        client = (genai.Client(api_key=self.api_key, http_options=http_options)
                  if self.api_key else genai.Client(http_options=http_options))

        response = client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
            ),
        )
        # Token consumati, per l'osservabilità; None se il campo è assente.
        self._last_tokens = getattr(
            getattr(response, "usage_metadata", None), "total_token_count", None)
        return response.text or ""
