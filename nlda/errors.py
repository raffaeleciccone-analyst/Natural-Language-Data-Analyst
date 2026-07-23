"""
Eccezioni applicative.

Un guasto ha bisogno di un TIPO, non di un messaggio: sul tipo si decide (si
ritenta? si mostra? si blocca?), mentre il messaggio serve solo a chi legge. Il
progetto ha già imparato la lezione sul versante dei risultati d'esecuzione, dove
`ExecutionFailure.kind` ha sostituito il prefisso "Errore" di una stringa; qui la
stessa idea vale per ciò che viene sollevato.

`NLDAError` è la radice comune: permette a un chiamante di distinguere un guasto
previsto dall'applicazione da un bug di programmazione, che deve continuare a
propagarsi invece di essere inghiottito da un `except Exception`. Vi appartiene
anche `UnsafeCodeError` (definita nel modulo sandbox, dove viene sollevata): un
rifiuto della sandbox è un guasto previsto, non un bug.

Nota di confine: NON tutti i fallimenti passano di qui. La pipeline di esecuzione
del codice generato (validazione → sottoprocesso → timeout) comunica il proprio
esito con il RISULTATO tipizzato `ExecutionFailure(kind=...)`, non sollevando
eccezioni; è una scelta deliberata (vedi results.py). Le eccezioni di questo file
servono ai guasti che attraversano più livelli — su tutti, quello del provider.
"""


class NLDAError(Exception):
    """Radice di tutti i guasti previsti dall'applicazione."""


class ProviderError(NLDAError):
    """
    La comunicazione con il modello è fallita (rete, credenziali, quota, modello
    inesistente) dopo che il provider ha già esaurito i propri tentativi.

    Distinguerla conta: nessuna riformulazione del prompt la risolve, quindi il
    turno va chiuso subito invece di essere ritentato a spese di altre chiamate.
    Le sottoclassi affinano *perché* è fallita, così la UI può dare all'utente
    un'indicazione utile (correggi la chiave / riprova) invece del testo grezzo
    dell'SDK.
    """

    def __init__(self, provider: str, original: Exception):
        self.provider = provider
        self.original = original
        super().__init__(self._message())

    def _message(self) -> str:
        return f"comunicazione con il provider '{self.provider}' fallita: {self.original}"

    @classmethod
    def classify(cls, provider: str, original: Exception) -> "ProviderError":
        """
        Sceglie la sottoclasse giusta dal guasto originale, così il chiamante non
        deve conoscere gli status HTTP dei vari SDK: 401/403 → credenziali,
        timeout/connessione → irraggiungibile, tutto il resto → guasto generico.
        Riusa `_http_status` (l'unica estrazione di status del progetto) e, per gli
        errori senza risposta HTTP, ricade sul nome del tipo (APITimeoutError,
        APIConnectionError, TimeoutError…), che è indipendente dall'SDK.
        """
        # Import locale: tiene errors.py svincolato dai provider a livello di modulo
        # (è importato molto presto) ed evita qualunque ciclo.
        from nlda.providers.base import _http_status

        status = _http_status(original)
        if status in (401, 403):
            return ProviderAuthError(provider, original)
        name = type(original).__name__.lower()
        if status in (408, 504) or "timeout" in name or "connection" in name:
            return ProviderTimeoutError(provider, original)
        return cls(provider, original)


class ProviderAuthError(ProviderError):
    """
    Le credenziali sono state rifiutate (401/403): API key assente, errata o senza
    i permessi per il modello richiesto. Né un retry né una riformulazione aiutano
    — l'utente deve correggere la chiave, e il messaggio glielo dice esplicitamente.
    """

    def _message(self) -> str:
        return (f"il provider '{self.provider}' ha rifiutato le credenziali: "
                f"controlla la API key. ({self.original})")


class ProviderTimeoutError(ProviderError):
    """
    Il provider non ha risposto in tempo o è irraggiungibile (timeout di rete o
    connessione fallita). A differenza dell'auth qui riprovare più tardi può
    riuscire, e il messaggio lo suggerisce.
    """

    def _message(self) -> str:
        return (f"il provider '{self.provider}' non ha risposto in tempo o è "
                f"irraggiungibile: riprova tra poco. ({self.original})")
