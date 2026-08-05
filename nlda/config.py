"""
Configurazione centralizzata dell'applicazione.

Fonte unica di verità per i parametri che prima erano sparsi tra executor,
worker, provider e main. I valori hanno default sensati e sono sovrascrivibili
da variabile d'ambiente (utile in deploy senza toccare il codice).

Uso:
    from nlda.config import settings
    settings.exec_timeout        # -> int
    settings.sandbox_subprocess  # -> bool

`settings` è un'istanza pronta; per i test si può costruire un Settings ad hoc.
"""
import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Parametri di runtime. Immutabile: si legge, non si muta a metà esecuzione."""

    # --- Sandbox di esecuzione ---
    # Esecuzione in sottoprocesso isolato con timeout (chiude i DoS da loop/allocazioni).
    sandbox_subprocess: bool = True
    exec_timeout: int = 12                # secondi di wall-time per il codice generato
    memory_limit_mb: int = 1500           # cap RAM del worker (solo POSIX; vedi nota sotto)
    # Se il sottoprocesso non è avviabile, si può ripiegare sull'esecuzione in-process.
    # ATTENZIONE: l'in-process NON ha né timeout né limite di memoria. In deploy
    # pubblico (demo) va tenuto False per "fallire chiuso" invece di degradare.
    allow_inprocess_fallback: bool = True

    # --- Limiti sul file caricato ---
    # Non sono limiti di MEMORIA: misurato che 3 milioni di righe costano ~340 MB
    # di picco, dentro i 2 GB del container. Sono limiti di USABILITÀ: oltre queste
    # soglie ogni domanda richiede più di un secondo e il report iniziale diversi,
    # e un'app che risponde così non è un'app che si vuole mostrare. Il cap di
    # upload di Streamlit (25 MB) bounda già i CSV, ma non i formati compressi:
    # un .xlsx da 25 MB può contenere milioni di righe.
    max_rows: int = 2_000_000
    max_columns: int = 500

    # Quanti MB può pesare il file CARICATO. È un limite di trasferimento, non di
    # memoria: dice quanto si è disposti a ricevere, non quanto si riesce ad
    # analizzare (quello è `max_dataset_ram_mb`, qui sotto). I due non si deducono
    # l'uno dall'altro — un CSV di numeri da 9 MB diventa 36 MB in memoria, un CSV
    # di testo da 20 MB ne occupa altrettanti — e per questo si dichiarano
    # entrambi a chi carica.
    max_upload_mb: int = 25

    # --- Quanta memoria può occupare UN dataset letto ---
    # Righe e colonne non dicono quanto costa un file: 50.000 righe per 200
    # colonne di interi stanno in 20 MB su disco e diventano 80 MB in memoria,
    # dentro entrambi i limiti qui sopra. E il costo vero è il PICCO per
    # costruire la tabella, misurato ~2,6 volte la tabella stessa: 20 MB di CSV
    # ne chiedevano 207 al processo. Su un container da 512 MB questo tetto è
    # ciò che sta fra un file grande e l'applicazione uccisa dal sistema —
    # perciò la demo lo abbassa in `render.yaml`. Il default è tarato sul
    # `docker-compose` da 2 GB e sullo sviluppo in locale.
    max_dataset_ram_mb: int = 256

    # --- Quanta RAM può tenere occupata il magazzino dell'API ---
    # I dataset caricati restano in memoria fra una richiesta e l'altra
    # (`nlda/api/store.py`). Questo è il tetto sulla loro somma: superato, si
    # sfratta il meno usato di recente. Va tarato sul CONTAINER, non sulla
    # macchina: il piano su cui gira la demo ne dà 512 in tutto, di cui ~130
    # se li prende l'immagine e ~100 la riserva calda della sandbox — perciò lì
    # `MAX_STORE_RAM_MB` è abbassato in `render.yaml`. Il default vale per il
    # `docker-compose` da 2 GB e per lo sviluppo in locale.
    store_ram_mb: int = 256

    # --- Provider LLM ---
    request_timeout: float = 30.0         # timeout per singola chiamata all'API del modello
    max_retries: int = 1                  # tentativi extra oltre al primo, su errori transitori
    retry_backoff: float = 0.8            # secondi base per il backoff esponenziale

    # Nota: i limiti della demo pubblica NON stanno qui. Arrivano dai secrets di
    # Streamlit, non dall'ambiente, e vivono in `nlda.demo.DemoLimits`: tenerne
    # una copia anche qui significava avere due fonti di verità, di cui una mai
    # letta da nessuno.

    # --- Logging ---
    log_level: str = "INFO"
    # "text" = una riga leggibile per lo sviluppo; "json" = una riga JSON per
    # riga, pensata per l'ingestione in un aggregatore di log in produzione/demo.
    log_format: str = "text"

    @classmethod
    def from_env(cls) -> "Settings":
        """Costruisce le impostazioni leggendo le variabili d'ambiente (con fallback ai default)."""
        return cls(
            sandbox_subprocess=_env_bool("SANDBOX_SUBPROCESS", cls.sandbox_subprocess),
            exec_timeout=_env_int("EXEC_TIMEOUT", cls.exec_timeout),
            memory_limit_mb=_env_int("MEMORY_LIMIT_MB", cls.memory_limit_mb),
            allow_inprocess_fallback=_env_bool(
                "ALLOW_INPROCESS_FALLBACK", cls.allow_inprocess_fallback),
            max_rows=_env_int("MAX_ROWS", cls.max_rows),
            max_columns=_env_int("MAX_COLUMNS", cls.max_columns),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", cls.max_upload_mb),
            max_dataset_ram_mb=_env_int("MAX_DATASET_RAM_MB", cls.max_dataset_ram_mb),
            store_ram_mb=_env_int("MAX_STORE_RAM_MB", cls.store_ram_mb),
            request_timeout=_env_float("LLM_REQUEST_TIMEOUT", cls.request_timeout),
            max_retries=_env_int("LLM_MAX_RETRIES", cls.max_retries),
            retry_backoff=_env_float("LLM_RETRY_BACKOFF", cls.retry_backoff),
            log_level=os.getenv("LOG_LEVEL", cls.log_level).upper(),
            log_format=os.getenv("LOG_FORMAT", cls.log_format).strip().lower(),
        )


# Istanza condivisa dall'intera app (lettura sola).
settings = Settings.from_env()
