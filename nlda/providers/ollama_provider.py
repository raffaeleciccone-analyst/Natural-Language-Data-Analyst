from collections.abc import Iterator

from nlda.pricing import Usage

from .base import LLMProvider


def _chunk_content(chunk: object) -> str:
    """Testo di un chunk di streaming ollama (dict o oggetto tipizzato)."""
    msg = chunk["message"] if isinstance(chunk, dict) else getattr(chunk, "message", None)
    if isinstance(msg, dict):
        return msg.get("content") or ""
    return getattr(msg, "content", "") or ""


def _int_field(response: object, key: str) -> int | None:
    """Legge un conteggio intero sia se la risposta è un dict sia se è un oggetto
    tipizzato (la libreria ollama ha restituito entrambe le forme tra le versioni).
    Qualsiasi cosa non sia un int diventa None: la metrica è opzionale, mai fatale."""
    val = response.get(key) if isinstance(response, dict) else getattr(response, key, None)
    return val if isinstance(val, int) else None


class OllamaProvider(LLMProvider):
    """Modelli locali serviti da Ollama (llama3, mistral, qwen2.5, ...)."""

    LOCAL = True  # gira sul tuo hardware: costo per token 0, non "sconosciuto"

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        import ollama  # import lazy: richiesto solo se usi questo provider

        response = ollama.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self.temperature},
        )
        # Token consumati: prompt (input) e generazione (output) separati.
        self._last_usage = Usage(_int_field(response, "prompt_eval_count"),
                                 _int_field(response, "eval_count"))
        return response["message"]["content"]

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        import ollama  # import lazy

        self._last_usage = Usage()
        for chunk in ollama.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self.temperature},
            stream=True,
        ):
            # I conteggi arrivano nell'ultimo chunk (done=True): si aggiornano se ci sono.
            prompt_tok = _int_field(chunk, "prompt_eval_count")
            gen_tok = _int_field(chunk, "eval_count")
            if prompt_tok is not None or gen_tok is not None:
                self._last_usage = Usage(prompt_tok, gen_tok)
            testo = _chunk_content(chunk)
            if testo:
                yield testo
