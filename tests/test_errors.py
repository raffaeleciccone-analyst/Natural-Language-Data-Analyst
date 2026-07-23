"""
Test della gerarchia delle eccezioni e del classificatore di ProviderError.

Il punto: `ProviderError.classify` deve dedurre il TIPO di guasto dal solo
originale, senza conoscere gli SDK — per status HTTP (401/403 → auth, 408/504 →
timeout) o, quando manca la risposta HTTP, dal nome del tipo (APITimeoutError,
APIConnectionError…). Sul tipo la UI decide che messaggio mostrare.
"""
import pandas as pd
import pytest

from nlda.agent import DataAgent
from nlda.errors import (
    NLDAError,
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
)
from nlda.providers.base import LLMProvider
from nlda.sandbox import UnsafeCodeError


class _HTTPError(Exception):
    """Imita un'eccezione SDK: espone uno status HTTP."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


# Nomi che ricalcano quelli reali degli SDK (openai/anthropic): niente status,
# ma il tipo dice tutto. Il classificatore ci ricade quando lo status manca.
class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


# --- Struttura della gerarchia ---------------------------------------------------
def test_le_sottoclassi_provider_sono_nlda_error():
    # Un except NLDAError le riconosce tutte come guasti previsti, non come bug.
    for exc in (ProviderError, ProviderAuthError, ProviderTimeoutError):
        assert issubclass(exc, NLDAError)
    assert issubclass(ProviderAuthError, ProviderError)
    assert issubclass(ProviderTimeoutError, ProviderError)


def test_unsafe_code_error_e_nella_gerarchia():
    # #18: il rifiuto della sandbox è un guasto previsto, non un Exception nudo.
    assert issubclass(UnsafeCodeError, NLDAError)


# --- Classificazione per status HTTP ---------------------------------------------
@pytest.mark.parametrize("status", [401, 403])
def test_status_di_autenticazione_diventa_auth_error(status):
    e = ProviderError.classify("openai", _HTTPError(status))
    assert isinstance(e, ProviderAuthError)
    assert "API key" in str(e)          # messaggio azionabile per l'utente
    assert e.provider == "openai"


@pytest.mark.parametrize("status", [408, 504])
def test_status_di_timeout_diventa_timeout_error(status):
    e = ProviderError.classify("groq", _HTTPError(status))
    assert isinstance(e, ProviderTimeoutError)
    assert "riprova" in str(e).lower()


@pytest.mark.parametrize("status", [400, 404, 429, 500])
def test_altri_status_restano_provider_error_generico(status):
    # 400/404/429/500 non sono né credenziali né timeout: guasto generico.
    e = ProviderError.classify("gemini", _HTTPError(status))
    assert type(e) is ProviderError


# --- Classificazione per tipo, quando manca lo status HTTP -----------------------
def test_timeout_senza_status_riconosciuto_dal_nome():
    e = ProviderError.classify("anthropic", APITimeoutError("scaduto"))
    assert isinstance(e, ProviderTimeoutError)


def test_connessione_senza_status_riconosciuta_dal_nome():
    e = ProviderError.classify("anthropic", APIConnectionError("host irraggiungibile"))
    assert isinstance(e, ProviderTimeoutError)


def test_errore_generico_senza_status_resta_provider_error():
    # RuntimeError qualsiasi: nessuno status, nome senza timeout/connection.
    e = ProviderError.classify("ollama", RuntimeError("qualcosa"))
    assert type(e) is ProviderError


def test_original_e_provider_sono_conservati():
    orig = _HTTPError(401)
    e = ProviderError.classify("openai", orig)
    assert e.original is orig
    assert e.provider == "openai"


# --- Integrazione: l'agente solleva la sottoclasse giusta ------------------------
class _ProviderAuthRotto(LLMProvider):
    """Provider che fallisce con un 401, come una chiave errata."""

    def __init__(self):
        super().__init__(model_name="rotto")

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        raise _HTTPError(401)


def test_agente_solleva_provider_auth_error_su_401(monkeypatch):
    import nlda.providers.base as base_mod
    from nlda.config import Settings

    # Niente retry/attese: un 401 non è ritentabile e non deve rallentare la suite.
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=0, retry_backoff=0.0))

    agente = DataAgent(provider=_ProviderAuthRotto())
    with pytest.raises(ProviderAuthError):
        agente.ask_code("qual è il totale?", pd.DataFrame({"a": [1, 2]}))
