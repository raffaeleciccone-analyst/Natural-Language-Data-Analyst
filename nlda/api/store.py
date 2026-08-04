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
* **occupa RAM** — da qui i due tetti, che sono il vero motivo per cui questo
  modulo esiste invece di un dizionario globale. Il tetto sul NUMERO di tabelle
  da solo non bastava: otto dataset entro i limiti del caricatore (3 milioni di
  righe ≈ 340 MB di picco, misurati e dichiarati in `config.py`) fanno 2,7 GB,
  cioè più del container da 2 GB e cinque volte i 512 MB del piano su cui gira
  la demo. Contare le tabelle non è contare la memoria: si contano i byte.

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

from nlda.config import settings
from nlda.log import get_logger

log = get_logger(__name__)

MAX_DATASET = 8          # quante tabelle tenere insieme
TTL_SECONDI = 60 * 60    # oltre un'ora di inattività, si libera la memoria


def _byte_occupati(df: pd.DataFrame) -> int:
    """
    Quanta RAM occupa davvero questa tabella.

    `deep=True` perché con le colonne di tipo `object` il conto svelto misura i
    puntatori e non le stringhe: sbaglierebbe proprio dove il dataset è grosso.
    Costa 4 ms sul dataset dimostrativo e si paga UNA volta per caricamento, non
    a ogni richiesta.
    """
    return int(df.memory_usage(deep=True).sum())


@dataclass
class Voce:
    """Un dataset in attesa delle prossime domande."""

    df: pd.DataFrame
    etichetta: str
    creata: float = field(default_factory=time.monotonic)
    ultimo_uso: float = field(default_factory=time.monotonic)
    byte: int = 0
    # Ordine d'uso, non istante d'uso: chi sfratta ha bisogno di sapere CHI è
    # venuto prima, e `ultimo_uso` non sa dirlo sotto la risoluzione dell'orologio
    # — su Windows `monotonic` avanza a scatti di ~15 ms, e due caricamenti nello
    # stesso scatto risulterebbero contemporanei. Il tempo resta per la scadenza,
    # dove la domanda è "quanto fa?" e la risoluzione non conta.
    uso: int = 0


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

    def __init__(self, capienza: int = MAX_DATASET, ttl: float = TTL_SECONDI,
                 ram_mb: int | None = None):
        self._voci: dict[str, Voce] = {}
        self._lock = threading.Lock()
        self._contatore = 0      # scandisce l'ordine d'uso; vedi `Voce.uso`
        self.capienza = capienza
        self.ttl = ttl
        self.ram_massima = (settings.store_ram_mb if ram_mb is None else ram_mb) * 1024 * 1024

    def _scadute(self, ora: float) -> list[str]:
        return [k for k, v in self._voci.items() if ora - v.ultimo_uso > self.ttl]

    def _byte_totali(self) -> int:
        return sum(v.byte for v in self._voci.values())

    def _prossimo_uso(self) -> int:
        """Il numero d'ordine successivo. Va chiamato col lock in mano."""
        self._contatore += 1
        return self._contatore

    def _troppo(self) -> str:
        """Quale tetto è sforato, se lo è: la ragione dello sfratto, non solo il fatto."""
        if len(self._voci) > self.capienza:
            return "capienza"
        if self._byte_totali() > self.ram_massima:
            return "memoria"
        return ""

    def aggiungi(self, chiave: str, df: pd.DataFrame, etichetta: str) -> str:
        byte = _byte_occupati(df)
        with self._lock:
            ora = time.monotonic()
            for k in self._scadute(ora):
                del self._voci[k]
            self._voci[chiave] = Voce(df=df, etichetta=etichetta, byte=byte,
                                      uso=self._prossimo_uso())
            # Sfratto il meno usato di recente finché non si rientra nei tetti.
            # Il minimo lineare basta: le voci sono al più una manciata, e una
            # struttura ordinata in più si pagherebbe in complessità per
            # risparmiare microsecondi.
            while (ragione := self._troppo()) and len(self._voci) > 1:
                vecchio = min(self._voci, key=lambda k: self._voci[k].uso)
                sfrattato = self._voci.pop(vecchio)
                log.info("magazzino_sfratto",
                         extra={"chiave": vecchio, "ragione": ragione,
                                "byte": sfrattato.byte})
            if self._byte_totali() > self.ram_massima:
                # Rimasta una sola voce e sfora da sola: sfrattarla lascerebbe
                # l'utente senza il dataset che ha appena caricato, e la memoria
                # è comunque già occupata dal DataFrame che ci hanno passato. Si
                # tiene, ma lo si dice: se compare nei log, il tetto è tarato
                # male o il caricatore ammette file troppo grandi per questo host.
                log.warning("magazzino_oltre_il_tetto",
                            extra={"chiave": chiave, "byte": byte,
                                   "tetto_byte": self.ram_massima})
            log.info("magazzino_aggiunto",
                     extra={"chiave": chiave, "righe": len(df), "byte": byte,
                            "in_memoria": len(self._voci),
                            "byte_totali": self._byte_totali()})
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
            v.uso = self._prossimo_uso()
            return v

    def __len__(self) -> int:
        with self._lock:
            return len(self._voci)

    def byte_totali(self) -> int:
        """La RAM occupata dai dataset in memoria, per i test e per chi osserva."""
        with self._lock:
            return self._byte_totali()

    def svuota(self) -> None:
        """Solo per i test: riporta il magazzino allo stato iniziale."""
        with self._lock:
            self._voci.clear()


# Istanza condivisa dall'applicazione. Una sola, come il processo che la serve.
magazzino = MagazzinoDataset()
