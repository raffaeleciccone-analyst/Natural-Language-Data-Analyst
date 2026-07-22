import os
import time
from abc import ABC, abstractmethod

from nlda.config import settings
from nlda.log import get_logger

log = get_logger(__name__)


class LLMProvider(ABC):
    """
    Interfaccia comune per tutti i provider LLM.

    Per aggiungere un nuovo provider basta creare una sottoclasse che implementa
    `_call()` (la chiamata "grezza" all'API) e registrarla nel factory
    (providers/__init__.py). Il metodo pubblico `generate()` è un *template method*
    condiviso: applica a TUTTI i provider timeout logico, retry con backoff sugli
    errori transitori e logging della latenza — così la resilienza sta in un posto
    solo invece di essere duplicata (o dimenticata) in ogni provider.

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

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Chiama il modello con retry/backoff sugli errori transitori e ne misura la
        latenza. Delega la chiamata vera a `_call()`; rilancia l'ultima eccezione
        se tutti i tentativi falliscono (l'agente la trasforma in errore leggibile).
        """
        attempts = max(1, settings.max_retries + 1)
        last_exc: Exception | None = None
        for i in range(1, attempts + 1):
            t0 = time.monotonic()
            try:
                text = self._call(system_prompt, user_prompt)
                log.info("%s/%s ok in %.2fs (tentativo %d/%d)",
                         self.name, self.model_name, time.monotonic() - t0, i, attempts)
                return text
            except Exception as e:  # noqa: BLE001 — vogliamo ritentare su qualunque errore transitorio
                last_exc = e
                log.warning("%s/%s errore al tentativo %d/%d: %s",
                            self.name, self.model_name, i, attempts, e)
                if i < attempts:
                    time.sleep(settings.retry_backoff * (2 ** (i - 1)))
        # Il ciclo esce solo dopo aver fallito ogni tentativo, quindi last_exc è
        # sempre valorizzata. Non si usa un assert: con `python -O` sparirebbe e
        # resterebbe un `raise None`, cioè un TypeError al posto dell'errore vero.
        if last_exc is None:  # pragma: no cover — irraggiungibile: attempts >= 1
            raise RuntimeError(f"{self.name}: nessun tentativo eseguito")
        raise last_exc

    @abstractmethod
    def _call(self, system_prompt: str, user_prompt: str) -> str:
        """Chiamata grezza all'API del provider. Implementata da ciascuna sottoclasse."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__
