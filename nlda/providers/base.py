import os
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator

from nlda.config import settings
from nlda.log import get_logger
from nlda.pricing import Usage, estimate_cost_usd

log = get_logger(__name__)

# Codici HTTP che restano ritentabili anche essendo 4xx: 408 (timeout lato
# server), 409 (conflitto transitorio), 425 (troppo presto), 429 (rate-limit).
# Tutto il resto della fascia 4xx è un errore della richiesta stessa — chiave
# sbagliata, modello inesistente, payload non valido — e ritentarlo identico
# fallirebbe uguale, bruciando tempo e quota.
_RETRYABLE_4XX = frozenset({408, 409, 425, 429})


def _http_status(exc: Exception) -> int | None:
    """
    Estrae il codice HTTP da un'eccezione di un SDK **senza importarne nessuno**
    (base.py resta libero da dipendenze: gli SDK si importano lazy in `_call`).
    openai/anthropic/ollama espongono `.status_code`, google-genai `.code`;
    alcuni lo tengono sotto `.response.status_code`. Se non c'è una risposta HTTP
    (errore di rete, timeout) ritorna None.
    """
    for attr in ("status_code", "code", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):  # `.code`/`.status` a volte è una stringa: la si salta
            return val
    val = getattr(getattr(exc, "response", None), "status_code", None)
    return val if isinstance(val, int) else None


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

    # True per i provider che girano sul TUO hardware (Ollama): il costo per token
    # è 0, non "sconosciuto". I provider cloud lo lasciano False e passano dal listino.
    LOCAL: bool = False

    # Token dell'ultima `_call`, quando l'SDK li riporta. Ogni sottoclasse lo
    # valorizza dalla propria risposta (input/output separati); `generate` lo logga
    # e ne stima il costo.
    _last_usage: Usage = Usage()

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

    def _is_retryable(self, exc: Exception) -> bool:
        """
        True se ha senso ritentare `exc`. Il default è condiviso e si basa sul
        codice HTTP quando c'è: un 4xx (401 chiave errata, 404 modello inesistente,
        400 richiesta malformata) è colpa della richiesta stessa — un secondo
        tentativo identico fallirebbe uguale — mentre 408/429, i 5xx e gli errori
        di rete senza risposta HTTP sono transitori. Un provider può ridefinirlo
        se il suo SDK segnala la ritentabilità in modo diverso.
        """
        status = _http_status(exc)
        if status is None:
            return True  # nessuna risposta HTTP: timeout o problema di rete, transitorio
        if 400 <= status < 500:
            return status in _RETRYABLE_4XX
        return True  # 5xx e casi anomali: si concede il ritentativo

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Chiama il modello con retry/backoff sugli errori transitori e ne misura la
        latenza. Delega la chiamata vera a `_call()`; rilancia l'ultima eccezione
        se tutti i tentativi falliscono (l'agente la trasforma in errore leggibile).
        Un errore classificato come NON ritentabile da `_is_retryable` interrompe
        subito il ciclo: inutile bruciare tentativi (e quota) su un 401 o un 404.
        """
        attempts = max(1, settings.max_retries + 1)
        last_exc: Exception | None = None
        for i in range(1, attempts + 1):
            t0 = time.monotonic()
            try:
                self._last_usage = Usage()  # ogni _call lo rivalorizza, se l'SDK lo riporta
                text = self._call(system_prompt, user_prompt)
                usage = self._last_usage
                cost = 0.0 if self.LOCAL else estimate_cost_usd(self.model_name, usage)
                log.info("provider_call_ok", extra={
                    "provider": self.name, "model": self.model_name,
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                    "attempt": i, "attempts": attempts,
                    "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                    "tokens": usage.total_tokens, "cost_usd": cost,
                })
                return text
            except Exception as e:  # noqa: BLE001 — la classificazione avviene in _is_retryable
                last_exc = e
                definitivo = i >= attempts or not self._is_retryable(e)
                log.warning("provider_call_error", extra={
                    "provider": self.name, "model": self.model_name,
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                    "attempt": i, "attempts": attempts,
                    "retryable": not definitivo, "error": str(e),
                })
                if definitivo:
                    break
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

    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        """
        Risposta a blocchi, per l'effetto "typewriter" in UI. Il default NON fa
        streaming reale: restituisce l'intera risposta in un colpo, così chi consuma
        (`st.write_stream`) funziona comunque. I provider con streaming SDK ridefiniscono.

        A differenza di `generate()` NON ritenta e non logga la latenza: lo streaming
        serve alla NARRATIVA (spiegazione, sintesi) — un complemento tollerante che
        già non solleva — non al codice, che passa sempre da `generate()`.
        """
        self._last_usage = Usage()
        yield self._call(system_prompt, user_prompt)

    def last_cost(self) -> "float | None":
        """Costo dell'ultima chiamata/stream: 0.0 se locale, None se non a listino."""
        return 0.0 if self.LOCAL else estimate_cost_usd(self.model_name, self._last_usage)

    @property
    def name(self) -> str:
        return self.__class__.__name__
