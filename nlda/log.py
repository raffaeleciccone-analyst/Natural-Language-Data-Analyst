"""
Logging centralizzato, con due formati e un contesto per-turno.

`get_logger(__name__)` restituisce un logger già pronto: il primo utilizzo
configura una sola volta handler, livello (`settings.log_level`) e formato
(`settings.log_format`), così i moduli non ripetono `basicConfig` né rischiano
handler duplicati.

Due formati, stessa origine dei dati:
* "text"  — una riga leggibile per lo sviluppo;
* "json"  — un oggetto JSON per riga, per l'ingestione in un aggregatore di log
  in produzione/demo (Grafana Loki, CloudWatch, …), dove i campi si filtrano.

Perché serve davvero: in produzione/demo è l'unico modo per capire perché una
domanda è fallita — quale regola della sandbox ha bocciato il codice, se è
scattato il fallback in-process, quanto ha impiegato il provider e quanti token
è costato. I campi strutturati (`provider`, `model`, `latency_ms`, `tokens`, …)
si passano con `extra={...}`; il `turn_id` che li correla lungo un intero turno
si imposta una volta con `bind_context(...)` e si propaga da solo a ogni riga.
"""
import contextvars
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from nlda.config import settings

_configured = False

# Contesto propagato automaticamente a ogni log emesso mentre è attivo, senza
# ripassarlo a ogni chiamata. contextvars è per-thread: si sposa con Streamlit,
# che serve una sessione per thread, e con l'esecuzione sincrona di un turno.
_log_context: contextvars.ContextVar["dict | None"] = contextvars.ContextVar(
    "nlda_log_context", default=None)


def _current_context() -> dict:
    """Contesto attivo, o un dict vuoto se non ne è stato impostato alcuno."""
    return _log_context.get() or {}

# Attributi standard di un LogRecord: tutto ciò che NON è qui è un campo extra
# passato dal chiamante, e va emesso. Calcolato dal record stesso per restare
# corretto tra le versioni di Python (3.12+ aggiunge taskName).
_STD_ATTRS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def _extra_fields(record: logging.LogRecord) -> dict:
    """Campi passati via `extra={...}`, cioè tutto ciò che non è standard nel record."""
    return {k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS}


class _JsonFormatter(logging.Formatter):
    """Una riga JSON per record: campi fissi + contesto di turno + extra."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_current_context())
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # default=str: un valore non serializzabile diventa la sua repr invece di
        # far esplodere il logging (un log non deve mai abbattere ciò che descrive).
        return json.dumps(payload, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    """Formato leggibile per lo sviluppo, con i campi strutturati in coda tra graffe."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = {**_current_context(), **_extra_fields(record)}
        if not fields:
            return base
        suffix = " ".join(f"{k}={v}" for k, v in fields.items())
        return f"{base} {{{suffix}}}"


def _make_formatter() -> logging.Formatter:
    if settings.log_format == "json":
        return _JsonFormatter()
    return _TextFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, settings.log_level, logging.INFO)
    root = logging.getLogger("nlda")  # namespace dedicato: non tocca i logger di terzi
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_make_formatter())
        root.addHandler(handler)
        root.propagate = False
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Logger applicativo sotto il namespace 'nlda' (configurato al primo uso)."""
    _configure_once()
    # Il pacchetto si chiama già 'nlda': senza questo taglio un modulo come
    # 'nlda.charts' diventerebbe 'nlda.nlda.charts'.
    suffix = name.split("nlda.", 1)[-1]
    return logging.getLogger(f"nlda.{suffix}")


@contextmanager
def bind_context(**fields: object) -> Iterator[None]:
    """
    Aggiunge campi al contesto di log per la durata del blocco (e per ogni log
    annidato). Usato per il `turn_id` che correla la generazione del codice, i
    tentativi di correzione e l'esecuzione dentro un unico turno.
    """
    token = _log_context.set({**_current_context(), **fields})
    try:
        yield
    finally:
        _log_context.reset(token)


def new_turn_id() -> str:
    """Identificatore breve e univoco per correlare i log di un singolo turno."""
    return uuid.uuid4().hex[:8]
