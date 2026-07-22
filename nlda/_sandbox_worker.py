"""
Worker eseguito in un interprete separato: legge (code, df) picklati da stdin,
esegue il codice nella sandbox e scrive il risultato come JSON su stdout.

L'asimmetria dei formati è deliberata: in ingresso la sorgente è il genitore
(fidata) e il pickle preserva i dtype del DataFrame; in uscita la sorgente è
questo processo, che ha appena eseguito codice generato da un LLM, quindi può
trasmettere solo dati inerti.

Isolare l'esecuzione in un processo dedicato consente di imporre un timeout
(dal genitore) e un limite di memoria, e aggiunge una barriera di processo attorno
al codice generato dall'LLM. Non importa mai main.py (nessun effetto Streamlit).
"""
import json
import os
import pickle
import sys

from nlda.config import settings
from nlda.log import get_logger

log = get_logger(__name__)


def _limit_memory(mb: int) -> None:
    """
    Limita la RAM del processo dove possibile.

    Nota di sicurezza importante: `resource.setrlimit(RLIMIT_AS)` esiste solo su
    POSIX. Su Windows il modulo `resource` non c'è, quindi questo è un NO-OP: il
    contenimento del worker su Windows resta il solo TIMEOUT (imposto dal genitore),
    non c'è un cap di memoria. È un limite noto e dichiarato, non una svista.
    """
    try:
        import resource  # POSIX-only
    except ImportError:
        log.info("Cap memoria non disponibile su questa piattaforma (Windows): "
                 "il worker è contenuto dal solo timeout.")
        return
    try:
        b = mb * 1024 * 1024
        # attributi POSIX-only: su Windows mypy non li conosce (serve 'attr-defined'),
        # su Linux invece li conosce (l'ignore sarebbe inutile → 'unused-ignore' evita
        # che warn_unused_ignores faccia fallire la CI). Coprire entrambe le piattaforme.
        resource.setrlimit(resource.RLIMIT_AS, (b, b))  # type: ignore[attr-defined, unused-ignore]
    except Exception as e:  # pragma: no cover
        log.warning("Impossibile impostare il cap memoria a %d MB: %s", mb, e)


def main() -> None:
    raw = sys.stdin.buffer.read()
    code, df = pickle.loads(raw)

    _limit_memory(settings.memory_limit_mb)

    # Manteniamo lo stdout reale per il risultato; durante l'esecuzione dirottiamo
    # eventuali stampe verso stderr, così su stdout finisce solo il pickle.
    real_stdout_fd = os.dup(1)
    os.dup2(2, 1)

    from nlda.executor import _run_code, serialize_result

    result = _run_code(code, df)
    out = serialize_result(result)

    # Il risultato torna al genitore come JSON, mai come pickle: questo processo
    # esegue codice generato dall'LLM e va considerato non fidato. Un pickle
    # ostile scritto qui verrebbe eseguito dal genitore, annullando l'isolamento.
    os.write(real_stdout_fd, json.dumps(out).encode("utf-8"))


if __name__ == "__main__":
    main()
