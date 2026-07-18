from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """Modelli locali serviti da Ollama (llama3, mistral, qwen2.5, ...)."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        import ollama  # import lazy: richiesto solo se usi questo provider

        response = ollama.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": self.temperature},
        )
        return response["message"]["content"]
