"""
Il ricordo dei calcoli già fatti, per le rotte che leggono e basta.

## Perché esiste

Cliccare una barra del grafico filtra la pagina; ricliccarla toglie il filtro. A
quel punto serve **esattamente il report di un secondo prima**, e il servizio lo
ricostruiva da capo: KPI, insight, tre figure Plotly, anteprima. Misurato sulla
demo il 5 agosto 2026: 1,5 secondi ogni volta, su un container che di CPU ne ha
un decimo. In locale la stessa richiesta costa 180 ms — la differenza è tutta
hardware, quindi la strada non è rendere il calcolo più svelto, è non rifarlo.

## Perché ricordare è sicuro qui

Il dataset è indirizzato dall'**impronta del suo contenuto** (`store.impronta`):
a parità di identificativo i dati sono gli stessi per definizione. Il report è
allora una funzione pura dei parametri della richiesta, e ricordarne il
risultato non è una scommessa sulla freschezza — è memoizzazione.

Due cose che questo modulo NON fa, di proposito:

* **non scade a tempo.** Una voce esce solo per far posto a un'altra: una
  scadenza servirebbe se i dati potessero cambiare sotto la stessa chiave, e non
  possono;
* **non ricorda gli errori.** Un guasto momentaneo diventerebbe permanente per
  tutti finché la voce non esce.

## Perché non un `functools.lru_cache`

Perché la chiave la si vuole scritta a mano e leggibile: `lru_cache` la
costruisce dagli argomenti, e basterebbe aggiungere un parametro alla rotta
dimenticandosene qui per servire a due richieste diverse la stessa risposta —
che è il difetto peggiore di una cache, perché ha l'aria di un dato giusto.
Inoltre `lru_cache` non si svuota per un singolo dataset e tiene un riferimento
forte agli argomenti, DataFrame compresi.
"""
import threading
from collections.abc import Callable
from typing import Any

from nlda.log import get_logger

log = get_logger(__name__)

# Una risposta di report pesa fino a ~130 KB: trenta voci sono ~4 MB, cioè meno
# dell'1% del container su cui gira la demo, e coprono l'andirivieni fra i
# filtri di più visitatori insieme.
MAX_VOCI = 30


class Ricordi:
    """Mappa chiave → risposta, di capienza fissa, con sfratto del meno recente."""

    def __init__(self, capienza: int = MAX_VOCI) -> None:
        self._voci: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.capienza = capienza

    def ottieni(self, chiave: str, calcola: Callable[[], Any]) -> Any:
        """
        La risposta per questa chiave: ricordata se c'è, calcolata se non c'è.

        Il calcolo gira FUORI dal lock: dura più di un secondo sul piano
        gratuito, e tenerlo dentro metterebbe in fila tutti gli altri visitatori
        dietro a uno solo — trasformando una cache in un collo di bottiglia. Due
        richieste identiche in volo insieme calcolano entrambe, e l'ultima
        vince: si spreca un calcolo raro invece di serializzare tutto il resto.
        """
        with self._lock:
            if chiave in self._voci:
                valore = self._voci.pop(chiave)      # rimesso in fondo: è il più recente
                self._voci[chiave] = valore
                log.info("cache_colpita", extra={"chiave": chiave})
                return valore

        valore = calcola()

        with self._lock:
            self._voci[chiave] = valore
            while len(self._voci) > self.capienza:
                # `dict` conserva l'ordine di inserimento: il primo è il più
                # vecchio per USO, perché ogni lettura riporta la voce in fondo.
                vecchia = next(iter(self._voci))
                del self._voci[vecchia]
        return valore

    def __len__(self) -> int:
        with self._lock:
            return len(self._voci)

    def svuota(self) -> None:
        """Solo per i test: riparte da zero fra un caso e l'altro."""
        with self._lock:
            self._voci.clear()


# Istanza condivisa dal processo, come il magazzino.
ricordi = Ricordi()
