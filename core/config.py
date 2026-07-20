"""
Configurazione centralizzata dell'applicazione.

Fonte unica di verità per i parametri che prima erano sparsi tra executor,
worker, provider e main. I valori hanno default sensati e sono sovrascrivibili
da variabile d'ambiente (utile in deploy senza toccare il codice).

Uso:
    from core.config import settings
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

    # --- Provider LLM ---
    request_timeout: float = 30.0         # timeout per singola chiamata all'API del modello
    max_retries: int = 1                  # tentativi extra oltre al primo, su errori transitori
    retry_backoff: float = 0.8            # secondi base per il backoff esponenziale

    # --- Demo pubblica ---
    demo_mode: bool = False
    demo_max_questions: int = 15

    # --- Logging ---
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        """Costruisce le impostazioni leggendo le variabili d'ambiente (con fallback ai default)."""
        return cls(
            sandbox_subprocess=_env_bool("SANDBOX_SUBPROCESS", cls.sandbox_subprocess),
            exec_timeout=_env_int("EXEC_TIMEOUT", cls.exec_timeout),
            memory_limit_mb=_env_int("MEMORY_LIMIT_MB", cls.memory_limit_mb),
            allow_inprocess_fallback=_env_bool(
                "ALLOW_INPROCESS_FALLBACK", cls.allow_inprocess_fallback),
            request_timeout=_env_float("LLM_REQUEST_TIMEOUT", cls.request_timeout),
            max_retries=_env_int("LLM_MAX_RETRIES", cls.max_retries),
            retry_backoff=_env_float("LLM_RETRY_BACKOFF", cls.retry_backoff),
            demo_mode=_env_bool("DEMO_MODE", cls.demo_mode),
            demo_max_questions=_env_int("DEMO_MAX_QUESTIONS", cls.demo_max_questions),
            log_level=os.getenv("LOG_LEVEL", cls.log_level).upper(),
        )


# Istanza condivisa dall'intera app (lettura sola).
settings = Settings.from_env()
