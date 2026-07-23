"""
Stima del costo in USD di una chiamata all'LLM, a partire dai token consumati.

⚠️ I prezzi sono listini PUBBLICI INDICATIVI (USD per 1 milione di token, come
`(input, output)`), tenuti a mano: cambiano spesso e NON sono un contratto. Vanno
riverificati sulla pagina prezzi del provider. Sono qui perché il valore della
funzione è il MECCANISMO — token → costo per turno, correlato al `turn_id` nei log
— non l'esattezza al centesimo di un numero che varia di settimana in settimana.

Regole di onestà:
- un modello NON a listino dà costo `None` (sconosciuto), mai un numero inventato;
- input e output pesano diversamente (l'output costa di più): si tengono separati;
- i provider locali (Ollama) costano 0 e non passano di qui — vedi `LLMProvider`.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    """Token di una singola chiamata. Input e output separati: hanno prezzi diversi."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        """Totale, o None se l'SDK non ha riportato alcun conteggio."""
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


# USD per 1M token, (input, output). Valori indicativi da listino pubblico,
# ~gennaio 2026: DA RIVERIFICARE prima di farci affidamento. Chiave = nome esatto
# del modello (quello in providers.DEFAULT_MODELS o digitato dall'utente).
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    # Anthropic
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-4-8": (15.00, 75.00),
    # Google
    "gemini-2.0-flash": (0.10, 0.40),
    # Groq (Llama) — tier a pagamento; il tier gratuito costa 0 ma qui si stima
    # il prezzo di listino, come proxy del costo "a regime".
    "llama-3.3-70b-versatile": (0.59, 0.79),
}


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    """
    Costo in USD della chiamata, o `None` se il modello non è a listino oppure se
    l'SDK non ha riportato i token. Un `None` significa "sconosciuto": non lo si
    confonda con 0, che invece è il costo reale di un modello locale.
    """
    prices = PRICES_PER_1M.get(model)
    if prices is None or usage.total_tokens is None:
        return None
    in_price, out_price = prices
    cost = ((usage.input_tokens or 0) * in_price
            + (usage.output_tokens or 0) * out_price) / 1_000_000
    return round(cost, 6)
