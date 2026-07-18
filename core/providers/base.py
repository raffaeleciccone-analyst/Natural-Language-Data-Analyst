from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """
    Interfaccia comune per tutti i provider LLM.
    Per aggiungere un nuovo provider basta creare una sottoclasse che
    implementa `generate()`, e registrarla nel factory (providers/__init__.py).
    """

    def __init__(self, model_name: str, temperature: float = 0.0):
        self.model_name = model_name
        self.temperature = temperature

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Invia i prompt al modello e restituisce il testo della risposta."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__
