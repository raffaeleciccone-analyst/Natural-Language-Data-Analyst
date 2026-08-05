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

# Quanta RAM possono occupare INSIEME i report ricordati.
#
# Si contano i BYTE e non le voci, ed è una correzione a questo stesso modulo
# scritta il giorno dopo averlo scritto: "trenta risposte da ~130 KB fanno 4 MB"
# misurava il solo dataset dimostrativo. Su un dataset da 300.000 righe una voce
# ne pesa **3,3**, perché la figura della distribuzione si porta dentro ogni
# valore della colonna misura — trenta voci sarebbero 96 MB, cioè più di quanto
# il magazzino accanto conceda a TUTTI i dataset messi insieme, sullo stesso
# container da 512 MB. Contare le voci è contare le tabelle un'altra volta, ed è
# l'errore che `store.py` ha già fatto e già pagato.
MAX_RAM_MB = 8


def _byte_occupati(valore: Any) -> int:
    """
    Quanto pesa una risposta ricordata.

    Si misura la sua forma JSON perché è quella la cosa grossa: dentro una
    figura Plotly gli array dei dati viaggiano già serializzati, e `sys.getsizeof`
    su un modello Pydantic misurerebbe il guscio invece del contenuto.
    """
    try:
        return len(valore.model_dump_json().encode())
    except AttributeError:
        # Non è un modello Pydantic: si ricade su una stima grossolana, che è
        # meglio di zero — una voce non misurabile non deve risultare gratis.
        return len(str(valore).encode())


class Ricordi:
    """Mappa chiave → risposta, con un tetto di MEMORIA e sfratto del meno recente."""

    def __init__(self, ram_mb: int = MAX_RAM_MB) -> None:
        self._voci: dict[str, Any] = {}
        self._byte: dict[str, int] = {}
        self._lock = threading.Lock()
        self.ram_massima = ram_mb * 1024 * 1024

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
        byte = _byte_occupati(valore)

        with self._lock:
            self._voci[chiave] = valore
            self._byte[chiave] = byte
            while sum(self._byte.values()) > self.ram_massima and len(self._voci) > 1:
                # `dict` conserva l'ordine di inserimento: il primo è il più
                # vecchio per USO, perché ogni lettura riporta la voce in fondo.
                vecchia = next(iter(self._voci))
                del self._voci[vecchia]
                del self._byte[vecchia]
            if byte > self.ram_massima:
                # Una risposta più grande dell'intero tetto: si tiene (è appena
                # stata calcolata e chi l'ha chiesta la sta ricevendo) ma lo si
                # dice, come fa il magazzino. Se compare nei log, il tetto è
                # tarato male o un report sta portando dentro troppi dati.
                log.warning("cache_voce_oltre_il_tetto",
                            extra={"chiave": chiave, "byte": byte,
                                   "tetto_byte": self.ram_massima})
        return valore

    def __len__(self) -> int:
        with self._lock:
            return len(self._voci)

    def byte_totali(self) -> int:
        """La RAM occupata dai ricordi, per i test e per chi osserva."""
        with self._lock:
            return sum(self._byte.values())

    def svuota(self) -> None:
        """Solo per i test: riparte da zero fra un caso e l'altro."""
        with self._lock:
            self._voci.clear()
            self._byte.clear()


# Istanza condivisa dal processo, come il magazzino.
ricordi = Ricordi()
