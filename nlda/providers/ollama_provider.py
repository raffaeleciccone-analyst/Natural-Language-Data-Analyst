from .base import LLMProvider


def _int_field(response: object, key: str) -> int | None:
    """Legge un conteggio intero sia se la risposta è un dict sia se è un oggetto
    tipizzato (la libreria ollama ha restituito entrambe le forme tra le versioni).
    Qualsiasi cosa non sia un int diventa None: la metrica è opzionale, mai fatale."""
    val = response.get(key) if isinstance(response, dict) else getattr(response, key, None)
    return val if isinstance(val, int) else None


class OllamaProvider(LLMProvider):
    """Modelli locali serviti da Ollama (llama3, mistral, qwen2.5, ...)."""

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
        # Token consumati (prompt + generazione), per l'osservabilità; None se assenti.
        prompt_tok = _int_field(response, "prompt_eval_count")
        gen_tok = _int_field(response, "eval_count")
        self._last_tokens = (
            (prompt_tok or 0) + (gen_tok or 0)
            if prompt_tok is not None or gen_tok is not None else None
        )
        return response["message"]["content"]
