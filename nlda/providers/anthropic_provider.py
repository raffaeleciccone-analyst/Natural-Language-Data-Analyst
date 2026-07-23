from collections.abc import Iterator

from nlda.config import settings
from nlda.pricing import Usage

from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    """
    Modelli LLM via API Anthropic.
    Nota: su alcuni modelli recenti i parametri di sampling
    (temperature/top_p/top_k) sono stati rimossi e restituiscono errore 400,
    quindi qui NON vengono passati. La determinazione si guida via prompt.
    """

    ENV_VAR = "ANTHROPIC_API_KEY"

    def _client(self):
        import anthropic  # import lazy

        # Se api_key è None, il client risolve le credenziali dall'ambiente
        return anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        message = self._client().messages.create(
            model=self.model_name,
            max_tokens=2048,  # sufficiente per una singola espressione/blocco pandas
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=settings.request_timeout,  # evita chiamate appese all'infinito
        )

        # Token consumati, input e output separati; None se assenti.
        usage = getattr(message, "usage", None)
        self._last_usage = Usage(getattr(usage, "input_tokens", None),
                                 getattr(usage, "output_tokens", None))

        # message.content è una lista di blocchi: prendiamo solo il testo
        return "".join(block.text for block in message.content if block.type == "text")

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        self._last_usage = Usage()
        with self._client().messages.stream(
            model=self.model_name,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            timeout=settings.request_timeout,
        ) as stream:
            yield from stream.text_stream
            usage = getattr(stream.get_final_message(), "usage", None)
            self._last_usage = Usage(getattr(usage, "input_tokens", None),
                                     getattr(usage, "output_tokens", None))
