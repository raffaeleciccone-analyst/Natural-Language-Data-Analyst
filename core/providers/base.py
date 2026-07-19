import os

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """
    Interfaccia comune per tutti i provider LLM.
    Per aggiungere un nuovo provider basta creare una sottoclasse che
    implementa `generate()`, e registrarla nel factory (providers/__init__.py).

    La API key viene risolta qui una volta sola: passata esplicitamente oppure
    letta dalla variabile d'ambiente indicata in ENV_VAR (una stringa o una
    tupla di nomi alternativi). I provider locali lasciano ENV_VAR = None.
    """

    # Nome della/e variabile/i d'ambiente con la API key (None = provider locale)
    ENV_VAR: "str | tuple[str, ...] | None" = None

    def __init__(self, model_name: str, temperature: float = 0.0,
                 api_key: str | None = None):
        self.model_name = model_name
        self.temperature = temperature
        self.api_key = api_key or self._key_from_env()

    def _key_from_env(self) -> str | None:
        names = (self.ENV_VAR,) if isinstance(self.ENV_VAR, str) else (self.ENV_VAR or ())
        for nm in names:
            val = os.getenv(nm)
            if val:
                return val
        return None

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Invia i prompt al modello e restituisce il testo della risposta."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__
