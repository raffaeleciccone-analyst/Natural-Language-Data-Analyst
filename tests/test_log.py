"""
Logging strutturato: formattazione JSON/testo, contesto di turno propagato, e il
fatto che `generate()` emetta davvero le metriche (latenza, token) correlate al
`turn_id`. Nessuna rete: il provider è finto.
"""
import json
import logging

import pytest

import nlda.providers.base as base_mod
from nlda.config import Settings
from nlda.log import (
    _JsonFormatter,
    _TextFormatter,
    bind_context,
    new_turn_id,
)
from nlda.pricing import Usage
from nlda.providers.base import LLMProvider


def _record(msg: str = "evento", level: int = logging.INFO, **extra) -> logging.LogRecord:
    r = logging.LogRecord("nlda.test", level, __file__, 1, msg, (), None)
    for k, v in extra.items():
        setattr(r, k, v)
    return r


# --- Formattazione JSON ----------------------------------------------------------
def test_json_formatter_campi_fissi_ed_extra():
    d = json.loads(_JsonFormatter().format(
        _record("provider_call_ok", provider="openai", latency_ms=12, tokens=None)))
    assert d["event"] == "provider_call_ok"
    assert d["level"] == "INFO"
    assert d["logger"] == "nlda.test"
    assert d["provider"] == "openai"
    assert d["latency_ms"] == 12
    assert d["tokens"] is None      # None è un valore informativo, non si omette
    assert "ts" in d


def test_json_formatter_valore_non_serializzabile_non_esplode():
    # Un log non deve mai abbattere ciò che descrive: un oggetto strano diventa str.
    d = json.loads(_JsonFormatter().format(_record(obj=object())))
    assert isinstance(d["obj"], str)


def test_json_formatter_include_la_traccia_su_eccezione():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord("nlda.test", logging.ERROR, __file__, 1,
                                "errore", (), sys.exc_info())
    d = json.loads(_JsonFormatter().format(rec))
    assert "ValueError: boom" in d["exc_info"]


# --- Contesto di turno -----------------------------------------------------------
def test_bind_context_propaga_e_si_ripulisce():
    fmt = _JsonFormatter()
    with bind_context(turn_id="abc123"):
        assert json.loads(fmt.format(_record()))["turn_id"] == "abc123"
    # Fuori dal blocco il campo non c'è più: nessuna perdita tra un turno e l'altro.
    assert "turn_id" not in json.loads(fmt.format(_record()))


def test_bind_context_annidato_unisce_e_ripristina():
    fmt = _JsonFormatter()
    with bind_context(turn_id="t1"):
        with bind_context(step="genera"):
            d = json.loads(fmt.format(_record()))
            assert d["turn_id"] == "t1" and d["step"] == "genera"
        d2 = json.loads(fmt.format(_record()))
        assert d2["turn_id"] == "t1" and "step" not in d2


# --- Formato testo ---------------------------------------------------------------
def test_text_formatter_appende_i_campi_in_coda():
    out = _TextFormatter("%(message)s").format(_record("ciao", provider="groq"))
    assert out.startswith("ciao ")
    assert "provider=groq" in out


def test_text_formatter_senza_campi_resta_pulito():
    assert _TextFormatter("%(message)s").format(_record("ciao")) == "ciao"


# --- Correlation-id --------------------------------------------------------------
def test_new_turn_id_breve_e_univoco():
    ids = {new_turn_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(i) == 8 for i in ids)


# --- Integrazione: generate() emette le metriche correlate al turno --------------
class _ProviderConToken(LLMProvider):
    """Provider finto che risponde subito e dichiara un consumo di token.
    model_name="gpt-4o-mini" così il costo passa dal listino (non è locale)."""

    def __init__(self, usage: Usage):
        super().__init__(model_name="gpt-4o-mini")
        self._usage = usage

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        self._last_usage = self._usage
        return "ok"


def _cattura_json(logger_name: str):
    """Attacca al logger un handler che formatta in JSON al volo (dentro il contesto
    attivo) e ne raccoglie l'output. Necessario perché il logger 'nlda' non propaga
    alla radice, quindi il caplog di pytest non lo vedrebbe."""
    logger = logging.getLogger(logger_name)
    righe: list[str] = []
    handler = logging.Handler()
    handler.setFormatter(_JsonFormatter())
    handler.emit = lambda record: righe.append(handler.format(record))  # type: ignore[method-assign]
    logger.addHandler(handler)
    return logger, handler, righe


def test_generate_logga_metriche_e_turn_id(monkeypatch):
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=0))
    logger, handler, righe = _cattura_json("nlda.providers.base")
    try:
        with bind_context(turn_id="turno1"):
            provider = _ProviderConToken(Usage(input_tokens=1000, output_tokens=500))
            assert provider.generate("s", "u") == "ok"
    finally:
        logger.removeHandler(handler)

    ok = next(json.loads(r) for r in righe if json.loads(r)["event"] == "provider_call_ok")
    assert ok["provider"] == "_ProviderConToken"
    assert ok["model"] == "gpt-4o-mini"
    assert ok["input_tokens"] == 1000 and ok["output_tokens"] == 500
    assert ok["tokens"] == 1500
    # gpt-4o-mini: (0.15, 0.60)/1M → 1000*0.15/1e6 + 500*0.60/1e6 = 0.00045
    assert ok["cost_usd"] == pytest.approx(0.00045)
    assert ok["turn_id"] == "turno1"          # correlazione col turno
    assert isinstance(ok["latency_ms"], int)


class _ProviderLocale(LLMProvider):
    """Provider locale (come Ollama): consuma token ma non costa nulla."""

    LOCAL = True

    def __init__(self):
        super().__init__(model_name="qwen2.5:3b")

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        self._last_usage = Usage(input_tokens=800, output_tokens=200)
        return "ok"


def test_provider_locale_logga_costo_zero_non_none(monkeypatch):
    # 0.0 è il costo REALE di un modello locale, diverso da None ("sconosciuto").
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=0))
    logger, handler, righe = _cattura_json("nlda.providers.base")
    try:
        _ProviderLocale().generate("s", "u")
    finally:
        logger.removeHandler(handler)

    ok = next(json.loads(r) for r in righe if json.loads(r)["event"] == "provider_call_ok")
    assert ok["cost_usd"] == 0.0
    assert ok["tokens"] == 1000


def test_generate_logga_errore_con_ritentabilita(monkeypatch):
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=0, retry_backoff=0.0))
    logger, handler, righe = _cattura_json("nlda.providers.base")

    class _Rotto(LLMProvider):
        def __init__(self):
            super().__init__(model_name="m")

        def _call(self, s: str, u: str) -> str:
            raise RuntimeError("giù")

    try:
        with pytest.raises(RuntimeError):
            _Rotto().generate("s", "u")
    finally:
        logger.removeHandler(handler)

    err = next(json.loads(r) for r in righe if json.loads(r)["event"] == "provider_call_error")
    assert err["provider"] == "_Rotto"
    assert err["error"] == "giù"
    assert err["retryable"] is False          # ultimo tentativo: definitivo
