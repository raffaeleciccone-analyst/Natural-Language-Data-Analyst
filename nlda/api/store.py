"""
Dove vive un dataset caricato, fra una richiesta HTTP e la successiva.

## Il problema che questo modulo risolve

Streamlit teneva il DataFrame in `st.session_state`: una sessione = una scheda del
browser, e il framework ci pensava lui. Un'API HTTP non ha sessioni: ogni richiesta
arriva sola e senza memoria. L'utente carica il file una volta e poi fa dieci
domande — il DataFrame deve stare da qualche parte in mezzo.

## La scelta, e i suoi limiti dichiarati

Una mappa in memoria con capienza e scadenza, chiusa da un lock.

Perché non un database o Redis: aggiungerebbero un servizio da gestire per tenere
un oggetto che è già in RAM e che scade in minuti. Perché non un file su disco:
serializzare e rileggere un DataFrame costa più che tenerlo, e comunque non
sopravvive a un container effimero.

I limiti li dichiaro invece di scoprirli in produzione:

* **non sopravvive al riavvio** del processo — chi ricarica la pagina dopo un
  deploy deve ricaricare il file. Accettabile per una demo, inaccettabile per un
  prodotto: lì servirebbe un archivio esterno;
* **non scala in orizzontale** — con due repliche dietro un bilanciatore, la
  seconda richiesta può finire sul processo che non ha il dataset. Va bene finché
  il deploy è un container solo, ed è il caso;
* **occupa RAM** — da qui la capienza massima, che è il vero motivo per cui questo
  modulo esiste invece di un dizionario globale.

## Perché la chiave è il contenuto e non un UUID

L'identificativo è l'impronta dei byte del file. Due conseguenze utili: ricaricare
lo stesso file non duplica la memoria, e l'identificativo è **riproducibile** —
un test può calcolarlo senza aver chiamato l'API. In cambio, due utenti che
caricano lo stesso file condividono la voce: qui va bene perché il contenuto è
identico per definizione, e non c'è nulla di per-utente nel valore memorizzato.
"""
import hashlib
import threading
import time
from dataclasses import dataclass, field

import pandas as pd

from nlda.log import get_logger

log = get_logger(__name__)

MAX_DATASET = 8          # quante tabelle tenere insieme
TTL_SECONDI = 60 * 60    # oltre un'ora di inattività, si libera la memoria


@dataclass
class Voce:
    """Un dataset in attesa delle prossime domande."""

    df: pd.DataFrame
    etichetta: str
    creata: float = field(default_factory=time.monotonic)
    ultimo_uso: float = field(default_factory=time.monotonic)


def impronta(dati: bytes, etichetta: str = "") -> str:
    """Identificativo riproducibile di un file: dipende solo dal suo contenuto."""
    h = hashlib.sha256(dati)
    if etichetta:
        h.update(etichetta.encode("utf-8"))
    return h.hexdigest()[:16]


class MagazzinoDataset:
    """
    Mappa identificativo -> DataFrame, con capienza e scadenza.

    Il lock protegge le operazioni composte (leggi-e-aggiorna, inserisci-e-sfratta):
    uvicorn serve le richieste su un pool di thread, quindi due domande simultanee
    sullo stesso dataset arrivano davvero in parallelo.
    """

    def __init__(self, capienza: int = MAX_DATASET, ttl: float = TTL_SECONDI):
        self._voci: dict[str, Voce] = {}
        self._lock = threading.Lock()
        self.capienza = capienza
        self.ttl = ttl

    def _scadute(self, ora: float) -> list[str]:
        return [k for k, v in self._voci.items() if ora - v.ultimo_uso > self.ttl]

    def aggiungi(self, chiave: str, df: pd.DataFrame, etichetta: str) -> str:
        with self._lock:
            ora = time.monotonic()
            for k in self._scadute(ora):
                del self._voci[k]
            self._voci[chiave] = Voce(df=df, etichetta=etichetta)
            # Sfratto il meno usato di recente finché non si rientra nella capienza.
            while len(self._voci) > self.capienza:
                vecchio = min(self._voci, key=lambda k: self._voci[k].ultimo_uso)
                del self._voci[vecchio]
                log.info("magazzino_sfratto", extra={"chiave": vecchio})
            log.info("magazzino_aggiunto",
                     extra={"chiave": chiave, "righe": len(df), "in_memoria": len(self._voci)})
            return chiave

    def prendi(self, chiave: str) -> "Voce | None":
        """La voce richiesta, rinfrescandone l'ultimo uso. `None` se assente o scaduta."""
        with self._lock:
            v = self._voci.get(chiave)
            if v is None:
                return None
            if time.monotonic() - v.ultimo_uso > self.ttl:
                del self._voci[chiave]
                return None
            v.ultimo_uso = time.monotonic()
            return v

    def __len__(self) -> int:
        with self._lock:
            return len(self._voci)

    def svuota(self) -> None:
        """Solo per i test: riporta il magazzino allo stato iniziale."""
        with self._lock:
            self._voci.clear()


# Istanza condivisa dall'applicazione. Una sola, come il processo che la serve.
magazzino = MagazzinoDataset()
