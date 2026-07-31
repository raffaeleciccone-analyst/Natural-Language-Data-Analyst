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
import pickle  # nosec B403
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


# Marcatore di prontezza. Un byte di controllo: non e' un inizio di JSON valido,
# quindi non puo' essere confuso col risultato nemmeno se qualcosa lo lasciasse
# passare. Il genitore lo consuma in `sandbox/pool.py`.
PRONTO = b""


def main() -> None:
    # L'ORDINE conta, ed è il motivo per cui il worker può essere pre-avviato.
    #
    # Prima si paga tutto ciò che non dipende dalla richiesta: import di pandas e
    # plotly (843 ms misurati, di cui 599 pandas e 197 plotly; l'interprete nudo
    # ne costa 30) e il cap di memoria. Solo DOPO ci si mette in attesa su stdin.
    #
    # Cosi' il genitore puo' tenere pronto un processo che ha gia' importato tutto
    # e dorme in attesa di lavoro: quando arriva una domanda, il costo fisso e'
    # gia' stato pagato altrove. Se gli import venissero dopo la lettura di stdin
    # — com'era prima — un processo pre-avviato non avrebbe importato nulla e il
    # preriscaldamento non servirebbe a niente.
    from nlda.sandbox.runner import _run_code, serialize_result

    _limit_memory(settings.memory_limit_mb)

    # Manteniamo lo stdout reale per il risultato; durante l'esecuzione dirottiamo
    # eventuali stampe verso stderr, così su stdout finisce solo il JSON.
    real_stdout_fd = os.dup(1)
    os.dup2(2, 1)

    # "Sono pronto": un byte che NON puo' iniziare un JSON, scritto dopo gli
    # import e prima di mettersi in attesa.
    #
    # Serve al genitore per distinguere due attese che prima erano una sola:
    # quanto ci mette QUESTO processo a esistere, e quanto ci mette il codice
    # generato a girare. Erano sommate dentro `EXEC_TIMEOUT`, cosi' su una
    # macchina lenta — 0,1 vCPU sul piano gratuito di un PaaS — andava in timeout
    # persino `len(df)`, e il messaggio d'errore accusava il codice dell'utente di
    # una lentezza che era dell'infrastruttura.
    os.write(real_stdout_fd, PRONTO)

    raw = sys.stdin.buffer.read()
    # La sorgente è il processo GENITORE, non l'utente: è lui a costruire questo
    # pickle da un DataFrame reale. La direzione pericolosa è l'opposta
    # (worker -> genitore) e infatti viaggia in JSON, mai in pickle.
    code, df = pickle.loads(raw)  # nosec B301

    result = _run_code(code, df)
    out = serialize_result(result)

    # Il risultato torna al genitore come JSON, mai come pickle: questo processo
    # esegue codice generato dall'LLM e va considerato non fidato. Un pickle
    # ostile scritto qui verrebbe eseguito dal genitore, annullando l'isolamento.
    os.write(real_stdout_fd, json.dumps(out).encode("utf-8"))


if __name__ == "__main__":
    main()
