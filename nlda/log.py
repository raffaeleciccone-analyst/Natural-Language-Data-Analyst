"""
Configurazione centralizzata del logging.

`get_logger(__name__)` restituisce un logger già pronto: il primo utilizzo
configura una sola volta l'handler e il livello (da `settings.log_level`), così
i moduli non devono ripetere `basicConfig` né rischiare handler duplicati.

Perché serve davvero: in produzione/demo è l'unico modo per capire perché una
domanda è fallita — quale regola della sandbox ha bocciato il codice, se è
scattato il fallback in-process, quanto ha impiegato il provider LLM.
"""
import logging

from nlda.config import settings

_configured = False


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    level = getattr(logging, settings.log_level, logging.INFO)
    root = logging.getLogger("nlda")  # namespace dedicato: non tocca i logger di terzi
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
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
