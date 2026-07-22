"""
Riserva calda di worker: toglie il costo fisso dell'avvio dal percorso critico.

## Il problema, misurato

Ogni domanda avviava un interprete nuovo e ne aspettava gli import: 843 ms prima
che il worker vedesse una riga di codice dell'utente (pandas 599, plotly 197,
interprete nudo 30). Con una risposta del modello che ne costa 2000-20000, era
fra il 5% e il 30% del tempo totale — pagato a ogni singola domanda, e senza
alcun rapporto con la dimensione dei dati: 877 ms su 200 mila righe, 983 su un
milione.

## La scelta: riserva calda, NON processo riutilizzato

Un pool classico terrebbe vivi N processi e li riuserebbe. Sarebbe piu' veloce,
ma barattarebbe una proprieta' di sicurezza che qui vale piu' della velocita':
**ogni esecuzione avviene in un processo fresco**. Il codice generato da un
modello puo' lasciarsi dietro di tutto — opzioni globali di pandas cambiate,
attributi di modulo riscritti, memoria non liberata — e la domanda successiva,
magari di un altro utente, lo erediterebbe.

Qui invece si tiene pronto **un processo che non ha ancora eseguito nulla**: ha
importato pandas e plotly, ha impostato il proprio cap di memoria, e dorme in
attesa su stdin. Quando arriva una domanda lo si usa, il worker esegue e muore
come prima; nel frattempo se ne prepara un altro in background.

Il risultato: stessa identica garanzia di isolamento, costo fisso spostato fuori
dall'attesa dell'utente.

## Cosa costa, dichiarato

Un processo in attesa occupa **~100 MB di RAM** (misurati: 102 MB), perche' ha
gia' importato pandas e plotly. E' il prezzo del preriscaldamento e va tenuto
presente dove la memoria e' contata: il container gira con `mem_limit: 2g`,
quindi ~5% del budget per non far aspettare l'utente. Si tiene UNA sola riserva,
non una per sessione, proprio per non moltiplicare quel costo.

## Cosa NON risolve

Il primo avvio resta a carico di chi lo chiede, a meno di chiamare `prewarm()`
all'apertura della pagina — cosa che l'app fa, cosi' la riserva si scalda mentre
l'utente legge il report.

Non riduce il costo del trasferimento del DataFrame, che viene ancora picklato a
ogni domanda: su un milione di righe sono ~38 MB per volta. Tenerlo nel worker fra
una domanda e l'altra richiederebbe un processo persistente, cioe' esattamente il
compromesso di sicurezza che questo progetto rifiuta.
"""
import atexit
import os
import subprocess  # nosec B404 — è la sandbox stessa
import sys
import threading
from pathlib import Path

import nlda
from nlda.log import get_logger

log = get_logger(__name__)

_COMANDO = [sys.executable, "-m", "nlda._sandbox_worker"]


def radice_progetto() -> str:
    """
    Cartella che CONTIENE il pacchetto `nlda`: e' quella che deve stare su
    PYTHONPATH perche' `python -m nlda._sandbox_worker` trovi il modulo. Si risale
    dal pacchetto e non da questo file, cosi' il calcolo non dipende da quanto e'
    annidato — errore gia' commesso una volta spostando il codice di un livello.
    """
    return str(Path(nlda.__file__).resolve().parent.parent)


def _ambiente(root: str) -> dict:
    return dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))


def _avvia() -> subprocess.Popen:
    """Avvia un worker che importa tutto e poi si mette in attesa su stdin."""
    root = radice_progetto()
    return subprocess.Popen(  # nosec B603 — argv fisso, nessuna shell, input via stdin
        _COMANDO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=root, env=_ambiente(root),
    )


class RiservaCalda:
    """
    Tiene pronto un worker gia' importato. Non e' un pool di processi riusati:
    ogni esecuzione ne consuma uno e ne fa preparare un altro.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pronto: subprocess.Popen | None = None
        self._in_preparazione = False

    # --- preparazione ---------------------------------------------------------
    def _prepara(self) -> None:
        """Avvia un worker di riserva. Gira in un thread: non deve far attendere nessuno."""
        try:
            proc = _avvia()
        except Exception as e:  # noqa: BLE001 — senza riserva si ripiega sull'avvio diretto
            log.warning("Impossibile preparare un worker di riserva: %s", e)
            with self._lock:
                self._in_preparazione = False
            return
        with self._lock:
            self._in_preparazione = False
            if self._pronto is None:
                self._pronto = proc
                return
        # Ne era gia' comparso un altro: si chiude questo invece di accumularlo.
        _termina(proc)

    def prewarm(self) -> None:
        """Chiede che una riserva sia pronta, senza attendere che lo sia."""
        with self._lock:
            if self._pronto is not None or self._in_preparazione:
                return
            self._in_preparazione = True
        threading.Thread(target=self._prepara, daemon=True, name="nlda-prewarm").start()

    # --- uso ------------------------------------------------------------------
    def _preleva(self) -> tuple[subprocess.Popen, bool]:
        """Restituisce (worker, era_caldo). Se la riserva manca, ne avvia uno subito."""
        with self._lock:
            proc, self._pronto = self._pronto, None
        if proc is not None and proc.poll() is None:
            return proc, True
        if proc is not None:                       # riserva morta nel frattempo
            log.info("Worker di riserva non piu' vivo: se ne avvia uno nuovo.")
        return _avvia(), False

    def esegui(self, payload: bytes, timeout: float) -> tuple[int, bytes, bytes]:
        """
        Esegue una richiesta e ritorna (returncode, stdout, stderr).

        Solleva `subprocess.TimeoutExpired` come farebbe `subprocess.run`, cosi'
        il chiamante non deve sapere se il worker era caldo o appena avviato.
        """
        proc, era_caldo = self._preleva()
        self.prewarm()   # il prossimo dev'essere pronto prima della prossima domanda
        try:
            stdout, stderr = proc.communicate(input=payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            _termina(proc)
            raise
        log.debug("Worker %s (caldo=%s) uscito con %s", proc.pid, era_caldo, proc.returncode)
        return proc.returncode, stdout, stderr

    # --- chiusura -------------------------------------------------------------
    def shutdown(self) -> None:
        """Chiude la riserva: un processo in attesa non deve sopravvivere all'app."""
        with self._lock:
            proc, self._pronto = self._pronto, None
        if proc is not None:
            _termina(proc)


def _termina(proc: subprocess.Popen) -> None:
    """Chiude un worker senza lasciare processi orfani né pipe aperte."""
    try:
        proc.kill()
        proc.communicate(timeout=5)
    except Exception as e:  # noqa: BLE001 — in chiusura non c'è nulla da recuperare
        log.debug("Chiusura del worker non pulita: %s", e)


# Istanza unica per processo: la riserva e' una risorsa del server, non della
# sessione. Streamlit ri-esegue lo script a ogni interazione, quindi tenerla in
# session_state significherebbe una riserva per scheda aperta.
riserva = RiservaCalda()
atexit.register(riserva.shutdown)
