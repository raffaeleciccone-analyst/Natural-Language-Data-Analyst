import os

from .base import LLMProvider


class AnthropicProvider(LLMProvider):
    """
    Modelli Claude via API Anthropic.
    Nota: su Claude Opus 4.8 / Sonnet 5 i parametri di sampling
    (temperature/top_p/top_k) sono stati rimossi e restituiscono errore 400,
    quindi qui NON vengono passati. La determinazione si guida via prompt.
    """

    def __init__(self, model_name: str = "claude-opus-4-8",
                 temperature: float = 0.0, api_key: str | None = None):
        super().__init__(model_name, temperature)
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic  # import lazy

        # Se api_key è None, il client risolve le credenziali dall'ambiente
        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()

        message = client.messages.create(
            model=self.model_name,
            max_tokens=2048,  # sufficiente per una singola espressione/blocco pandas
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # message.content è una lista di blocchi: prendiamo solo il testo
        return "".join(block.text for block in message.content if block.type == "text")
