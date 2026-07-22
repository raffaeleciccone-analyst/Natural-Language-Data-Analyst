from .openai_provider import OpenAIProvider


class GroqProvider(OpenAIProvider):
    """
    Modelli open (Llama, ...) serviti da Groq — inferenza molto veloce e tier
    gratuito generoso. L'API è compatibile con l'SDK OpenAI: basta puntare alla
    base_url di Groq, quindi eredita `_call()` da OpenAIProvider senza
    duplicare codice né aggiungere dipendenze.
    """

    ENV_VAR = "GROQ_API_KEY"
    base_url = "https://api.groq.com/openai/v1"
