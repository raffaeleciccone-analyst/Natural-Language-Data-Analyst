import os

from .base import LLMProvider


class GroqProvider(LLMProvider):
    """
    Modelli open (Llama, ...) serviti da Groq — inferenza molto veloce e tier
    gratuito generoso. L'API è compatibile con l'SDK OpenAI, quindi riusa lo
    stesso pacchetto (nessuna dipendenza aggiuntiva).
    """

    def __init__(self, model_name: str = "llama-3.3-70b-versatile",
                 temperature: float = 0.0, api_key: str | None = None):
        super().__init__(model_name, temperature)
        self.api_key = api_key or os.getenv("GROQ_API_KEY")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        from openai import OpenAI  # import lazy; Groq usa l'API compatibile OpenAI

        client = OpenAI(api_key=self.api_key, base_url="https://api.groq.com/openai/v1")

        response = client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
