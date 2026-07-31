"""
Test della riserva calda di worker.

La riserva esiste per togliere dal percorso critico gli ~840 ms di import che
ogni worker paga prima di poter eseguire alcunché. La proprietà che NON deve
perdere nel farlo è che ogni esecuzione avvenga in un processo **fresco**: qui si
verifica proprio quella, oltre al fatto che un guasto della riserva non impedisca
di lavorare.

I test usano processi finti dove possono: avviare interpreti veri renderebbe la
suite lenta e dipendente dal carico della macchina. L'unico giro davvero
end-to-end sta in `test_executor_ipc.py`, che passa dalla riserva reale.
"""
import subprocess

import pytest

from nlda._sandbox_worker import PRONTO
from nlda.sandbox.pool import RiservaCalda, radice_progetto


class _PipeFinta:
    """La `stdout` del processo, per il solo byte di prontezza."""

    def __init__(self, primo: bytes):
        self._primo = primo

    def read(self, n: int = -1) -> bytes:
        dato, self._primo = self._primo, b""
        return dato


class _ProcessoFinto:
    """Imita quel tanto di `subprocess.Popen` che la riserva usa."""

    def __init__(self, vivo: bool = True, returncode: int = 0, stdout: bytes = b"{}",
                 pronto: bytes = PRONTO):
        self.pid = 1234
        self._vivo = vivo
        self.returncode = returncode
        self._stdout = stdout
        self.usato = False
        self.ucciso = False
        # Il worker vero annuncia di aver finito gli import: qui si imita quello,
        # perche' la riserva non mette da parte un processo che non l'ha fatto.
        self.stdout = _PipeFinta(pronto)

    def poll(self):
        return None if self._vivo else 1

    def communicate(self, input=None, timeout=None):  # noqa: A002 — firma di Popen
        self.usato = True
        return self._stdout, b""

    def kill(self):
        self.ucciso = True
        self._vivo = False


@pytest.fixture
def riserva(monkeypatch):
    """Una riserva isolata, che non avvia processi veri."""
    creati = []

    def finto_avvia():
        p = _ProcessoFinto()
        creati.append(p)
        return p

    monkeypatch.setattr("nlda.sandbox.pool._avvia", finto_avvia)
    r = RiservaCalda()
    r.creati = creati  # type: ignore[attr-defined]
    return r


def _senza_thread(monkeypatch):
    """Esegue la preparazione della riserva in modo sincrono, per test deterministici."""
    class _ThreadImmediato:
        def __init__(self, target, **kw):
            self._target = target

        def start(self):
            self._target()

        def join(self, timeout=None):
            """Gia' eseguito in `start`: non c'e' nulla da attendere."""

    monkeypatch.setattr("nlda.sandbox.pool.threading.Thread", _ThreadImmediato)


# --- La proprietà che non si deve perdere --------------------------------------
def test_ogni_esecuzione_usa_un_processo_diverso(riserva, monkeypatch):
    """
    È il punto dell'intero progetto della riserva: si pre-avvia, NON si riusa.

    Un pool che riciclasse i processi sarebbe più veloce ma erediterebbe lo stato
    lasciato dal codice generato in precedenza — opzioni globali di pandas,
    attributi riscritti, memoria non liberata — e la domanda successiva, magari di
    un altro utente, lo troverebbe lì.
    """
    _senza_thread(monkeypatch)
    riserva.esegui(b"a", timeout=5)
    riserva.esegui(b"b", timeout=5)

    usati = [p for p in riserva.creati if p.usato]
    assert len(usati) == 2
    assert usati[0] is not usati[1]


# --- Preparazione ---------------------------------------------------------------
def test_prewarm_prepara_una_sola_riserva(riserva, monkeypatch):
    # Streamlit ri-esegue lo script a ogni interazione: se prewarm non fosse
    # idempotente, ogni click lascerebbe un processo in più.
    _senza_thread(monkeypatch)
    for _ in range(5):
        riserva.prewarm()
    assert len(riserva.creati) == 1


def test_senza_riserva_si_avvia_comunque(riserva, monkeypatch):
    # Il primo avvio, o una riserva non ancora pronta, non devono impedire di
    # lavorare: si paga il costo pieno, ma si risponde.
    _senza_thread(monkeypatch)
    returncode, stdout, _ = riserva.esegui(b"payload", timeout=5)
    assert returncode == 0 and stdout == b"{}"


def test_una_riserva_morta_viene_sostituita(riserva, monkeypatch):
    _senza_thread(monkeypatch)
    riserva.prewarm()
    morta = riserva.creati[0]
    morta._vivo = False          # il processo è uscito mentre attendeva

    riserva.esegui(b"payload", timeout=5)

    assert not morta.usato, "una riserva morta non deve essere usata"
    assert any(p.usato for p in riserva.creati[1:])


# --- Guasti ---------------------------------------------------------------------
def test_il_timeout_uccide_il_worker_e_risale(riserva, monkeypatch):
    _senza_thread(monkeypatch)

    def scade(self, input=None, timeout=None):  # noqa: A002
        raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    monkeypatch.setattr(_ProcessoFinto, "communicate", scade)
    with pytest.raises(subprocess.TimeoutExpired):
        riserva.esegui(b"payload", timeout=1)
    # Un worker scaduto non deve restare vivo a consumare risorse.
    assert any(p.ucciso for p in riserva.creati)


def test_se_la_preparazione_fallisce_non_solleva(riserva, monkeypatch):
    # Un guasto nel preparare la riserva è un problema di prestazioni, non di
    # correttezza: non deve propagarsi a chi sta facendo una domanda.
    _senza_thread(monkeypatch)

    def esplode():
        raise OSError("troppi processi")

    monkeypatch.setattr("nlda.sandbox.pool._avvia", esplode)
    riserva.prewarm()   # non deve sollevare


def test_shutdown_chiude_la_riserva(riserva, monkeypatch):
    _senza_thread(monkeypatch)
    riserva.prewarm()
    riserva.shutdown()
    assert riserva.creati[0].ucciso


# --- La radice del progetto, già sbagliata una volta ----------------------------
def test_la_radice_contiene_il_pacchetto():
    """
    Deve essere la cartella che CONTIENE `nlda`, non `nlda` stessa: è da lì che
    `python -m nlda._sandbox_worker` trova il modulo. Spostando il codice di un
    livello, un calcolo basato sul file invece che sul pacchetto si era rotto in
    silenzio — i test passavano perché il vecchio modulo esisteva ancora.
    """
    import pathlib
    assert (pathlib.Path(radice_progetto()) / "nlda" / "_sandbox_worker.py").exists()


# --- Il cap di memoria non deve diventare un guasto ----------------------------
def test_un_cap_di_memoria_inservibile_viene_alzato():
    """
    `RLIMIT_AS` limita lo SPAZIO DI INDIRIZZAMENTO, non la memoria occupata, e
    pandas ne riserva molto piu' di quanto usi. Con 256 MB — un valore che sembra
    generoso guardando i ~130 MB realmente occupati — il worker moriva prima di
    importare pandas, e OGNI domanda falliva con "possibile esaurimento memoria".

    Un tetto che impedisce qualunque esecuzione non protegge nulla: si alza al
    minimo utile e lo si dice nei log, invece di lasciare un'app che sembra rotta
    senza che niente spieghi perche'.
    """
    import logging

    from nlda import _sandbox_worker as worker

    applicati: list[int] = []
    detti: list[str] = []

    class _Ascolta(logging.Handler):
        def emit(self, record):
            detti.append(record.getMessage())

    # Il handler si aggancia al logger del MODULO: `caplog` non vede nulla perche'
    # `nlda.log` configura i logger senza propagazione verso la radice.
    ascoltatore = _Ascolta()
    worker.log.addHandler(ascoltatore)

    class _ResourceFinto:
        RLIMIT_AS = 9

        @staticmethod
        def setrlimit(_quale, coppia):
            applicati.append(coppia[0] // (1024 * 1024))

    import sys
    sys.modules["resource"] = _ResourceFinto  # type: ignore[assignment]
    try:
        worker._limit_memory(256)
        assert applicati == [worker._MINIMO_UTILE_MB]
        # Alzarlo in silenzio nasconderebbe una configurazione sbagliata.
        assert any("sotto il minimo utile" in m for m in detti)

        applicati.clear()
        worker._limit_memory(2048)
        assert applicati == [2048], "un valore sensato si applica intatto"
    finally:
        del sys.modules["resource"]
        worker.log.removeHandler(ascoltatore)
