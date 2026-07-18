"""
Worker eseguito in un interprete separato: legge (code, df, dark) picklati da stdin,
esegue il codice nella sandbox e scrive il risultato picklato su stdout.

Isolare l'esecuzione in un processo dedicato consente di imporre un timeout
(dal genitore) e un limite di memoria, e aggiunge una barriera di processo attorno
al codice generato dall'LLM. Non importa mai main.py (nessun effetto Streamlit).
"""
import os
import pickle
import sys


def _limit_memory(mb: int = 1500) -> None:
    """Limita la memoria del processo dove possibile (POSIX). Su Windows è no-op."""
    try:
        import resource
        b = mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (b, b))
    except Exception:
        pass


def main() -> None:
    raw = sys.stdin.buffer.read()
    code, df, dark = pickle.loads(raw)

    _limit_memory()

    # Manteniamo lo stdout reale per il risultato; durante l'esecuzione dirottiamo
    # eventuali stampe verso stderr, così su stdout finisce solo il pickle.
    real_stdout_fd = os.dup(1)
    os.dup2(2, 1)

    from core.executor import _run_code, set_theme, serialize_result

    set_theme(dark)
    result = _run_code(code, df)
    out = serialize_result(result)

    os.write(real_stdout_fd, pickle.dumps(out))


if __name__ == "__main__":
    main()
