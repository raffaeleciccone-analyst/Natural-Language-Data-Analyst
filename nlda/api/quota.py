"""
Quota della demo pubblica, lato API.

Il deploy pubblico gira con una chiave del manutentore: senza un tetto, ogni
visitatore ne spenderebbe il credito. La regola sta in `nlda.demo.DemoLimits`,
condivisa con l'app Streamlit; qui c'e' solo ciò che l'API deve fare in piu' —
contare, in un processo che serve piu' visitatori contemporaneamente.

## Due differenze rispetto a Streamlit, entrambe volute

**Si conta PRIMA della chiamata, non dopo.** Streamlit conta dopo, così un
provider irraggiungibile non consuma budget: può permetterselo perché le sue
sessioni sono seriali. Qui le richieste sono concorrenti, e contare dopo
significa che venti richieste simultanee passano tutte il controllo prima che
una sola abbia incrementato il contatore — il tetto non terrebbe. Si conta
prima: si è severi verso un guasto del provider, mai generosi verso il credito.

**La "sessione" è un indirizzo IP.** Streamlit ha una sessione vera; un'API no.
L'IP è l'unica cosa che distingua due visitatori senza chiedere loro un'identità,
ed è debole: dietro NAT o proxy aziendale molte persone condividono un
indirizzo, e chi ne cambia uno riparte da zero. Non è una difesa contro un abuso
determinato — quella richiederebbe un'identità, che una demo pubblica non ha.
Serve a evitare che un singolo visitatore esaurisca da solo la giornata di
tutti, ed è esattamente quanto dichiara di fare.

Il tetto che protegge davvero il credito resta quello GIORNALIERO, condiviso da
tutte le richieste del processo.
"""
import os
import threading
from datetime import date

from fastapi import HTTPException, Request

from nlda.demo import DemoLimits
from nlda.log import get_logger

log = get_logger(__name__)


def _intero(chiave: str, default: int) -> int:
    """Un valore malformato non deve togliere il tetto: si torna al default."""
    try:
        return int(os.getenv(chiave, "") or default)
    except ValueError:
        log.warning("%s non e' un intero: uso il default %d", chiave, default)
        return default


def limiti_da_ambiente() -> DemoLimits:
    """
    Gli stessi nomi che l'app Streamlit legge dai propri secrets.

    Un secondo vocabolario per la stessa regola ("API_DEMO_MODE"...) vorrebbe
    dire configurare due volte lo stesso deploy e scoprire dalla bolletta di aver
    dimenticato una delle due.
    """
    return DemoLimits(
        enabled=os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on"),
        max_questions=_intero("DEMO_MAX_QUESTIONS", 15),
        max_daily=_intero("DEMO_MAX_DAILY", 200),
    )


class Quota:
    """
    Contatore condiviso dal processo, azzerato ogni giorno.

    Vive in memoria: con piu' repliche ognuna ha il proprio tetto, quindi la
    spesa massima si moltiplica per il numero di repliche. Per la demo (una sola
    istanza) è corretto, e dirlo qui evita che un domani lo si scopra dal conto
    del provider. Un tetto davvero condiviso richiederebbe un archivio esterno.
    """

    def __init__(self, limiti: DemoLimits) -> None:
        self.limiti = limiti
        self._lock = threading.Lock()
        self._giorno = date.today()
        self._oggi = 0
        self._per_visitatore: dict[str, int] = {}

    @property
    def usate_oggi(self) -> int:
        with self._lock:
            self._ruota()
            return self._oggi

    def _ruota(self) -> None:
        """Nuovo giorno, nuovo credito. Da chiamare col lock già preso."""
        oggi = date.today()
        if self._giorno != oggi:
            self._giorno = oggi
            self._oggi = 0
            # Si svuota anche la mappa per visitatore: altrimenti crescerebbe
            # senza limite per tutta la vita del processo.
            self._per_visitatore.clear()

    def consuma(self, visitatore: str) -> None:
        """Registra una richiesta all'LLM, o solleva 429 se il budget è finito."""
        if not self.limiti.enabled:
            return

        with self._lock:
            self._ruota()
            usate_da_lui = self._per_visitatore.get(visitatore, 0)
            if self.limiti.has_budget(usate_da_lui, self._oggi):
                self._oggi += 1
                self._per_visitatore[visitatore] = usate_da_lui + 1
                return
            esaurito_il_giorno = self._oggi >= self.limiti.max_daily

        log.info("quota_esaurita {giornaliera=%s usate_oggi=%d}",
                 esaurito_il_giorno, self._oggi)
        # 429 e non 403: il budget torna: giornaliero domani, personale mai —
        # ma è comunque un limite di frequenza, non un divieto.
        raise HTTPException(
            status_code=429,
            detail=("La demo ha esaurito il budget giornaliero condiviso. Riprova domani, "
                    "oppure clona il repo per uso illimitato.")
            if esaurito_il_giorno else
            (f"Hai raggiunto il limite della demo ({self.limiti.max_questions} richieste). "
             "Clona il repo da GitHub per uso illimitato."),
        )


def visitatore(request: Request) -> str:
    """
    Chi sta chiedendo, per quel che l'API può saperne.

    Dietro il proxy di un PaaS `request.client.host` è l'indirizzo del proxy,
    uguale per tutti: senza `X-Forwarded-For` il limite per visitatore
    diventerebbe un secondo limite globale, molto piu' stretto del voluto. Si
    prende il PRIMO indirizzo della catena, che è il client originale; gli altri
    sono i proxy attraversati.

    L'intestazione la scrive il proxy e un client può falsificarla: chi vuole
    aggirare il limite personale ci riesce. È il motivo per cui il tetto vero è
    quello giornaliero, che nessuna intestazione può spostare.
    """
    inoltrato = request.headers.get("x-forwarded-for", "")
    if inoltrato:
        return inoltrato.split(",")[0].strip()
    return request.client.host if request.client else "sconosciuto"
