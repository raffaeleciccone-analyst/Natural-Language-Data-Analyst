from collections.abc import Iterator

from nlda.config import settings
from nlda.pricing import Usage

from .base import LLMProvider


class GeminiProvider(LLMProvider):
    """Modelli Google Gemini via SDK google-genai (gemini-2.0-flash, ...)."""

    ENV_VAR = ("GOOGLE_API_KEY", "GEMINI_API_KEY")

    def _client_and_config(self):
        from google import genai  # import lazy
        from google.genai import types

        # timeout in millisecondi (convenzione dell'SDK google-genai)
        http_options = types.HttpOptions(timeout=int(settings.request_timeout * 1000))
        client = (genai.Client(api_key=self.api_key, http_options=http_options)
                  if self.api_key else genai.Client(http_options=http_options))
        return client, types

    def _usage_from(self, response) -> Usage:
        meta = getattr(response, "usage_metadata", None)
        return Usage(getattr(meta, "prompt_token_count", None),
                     getattr(meta, "candidates_token_count", None))

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        client, types = self._client_and_config()
        response = client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
            ),
        )
        # Token consumati, input e output separati; None se il campo è assente.
        self._last_usage = self._usage_from(response)
        return response.text or ""

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        self._last_usage = Usage()
        client, types = self._client_and_config()
        for chunk in client.models.generate_content_stream(
            model=self.model_name,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
            ),
        ):
            meta = getattr(chunk, "usage_metadata", None)
            if meta is not None:
                self._last_usage = self._usage_from(chunk)
            if chunk.text:
                yield chunk.text
