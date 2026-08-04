"""Test della config (lettura da env), del factory dei provider e del retry/backoff comune."""
import pytest

import nlda.providers.base as base_mod
from nlda.config import Settings
from nlda.providers import DEFAULT_MODELS, available_providers, get_provider
from nlda.providers.base import LLMProvider
from nlda.providers.gemini_provider import GeminiProvider
from nlda.providers.ollama_provider import OllamaProvider
from nlda.providers.openai_provider import OpenAIProvider


def test_settings_default():
    s = Settings()
    assert s.exec_timeout == 12
    assert s.sandbox_subprocess is True
    assert s.allow_inprocess_fallback is True


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("EXEC_TIMEOUT", "5")
    monkeypatch.setenv("SANDBOX_SUBPROCESS", "false")
    monkeypatch.setenv("ALLOW_INPROCESS_FALLBACK", "no")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    s = Settings.from_env()
    assert s.exec_timeout == 5
    assert s.sandbox_subprocess is False
    assert s.allow_inprocess_fallback is False
    assert s.max_retries == 3


def test_il_tetto_di_ram_del_magazzino_si_tara_dall_ambiente(monkeypatch):
    """
    Va tarato sul container, non sulla macchina: il piano gratuito su cui gira la
    demo ne da' 512 MB in tutto, contro i 2 GB del docker-compose. Senza questa
    variabile il default sarebbe giusto in un posto e sbagliato nell'altro.
    """
    monkeypatch.setenv("MAX_STORE_RAM_MB", "96")
    assert Settings.from_env().store_ram_mb == 96


def test_settings_env_malformata_usa_default(monkeypatch):
    monkeypatch.setenv("EXEC_TIMEOUT", "non-un-numero")
    assert Settings.from_env().exec_timeout == 12


# --- Factory: è il punto in cui la UI traduce una scelta dell'utente in un oggetto ---
def test_provider_sconosciuto_solleva_valueerror():
    # Il messaggio elenca i provider validi: è quello che l'utente legge nell'app.
    with pytest.raises(ValueError, match="non supportato"):
        get_provider("provider-inesistente")


def test_ogni_provider_disponibile_ha_un_modello_di_default():
    # Un provider registrato senza default farebbe KeyError dentro get_provider.
    assert set(available_providers()) == set(DEFAULT_MODELS)


@pytest.mark.parametrize("nome", available_providers())
def test_modello_di_default_applicato_quando_non_specificato(nome):
    # Costruire un provider NON contatta nulla: l'import dell'SDK è lazy in _call.
    assert get_provider(nome).model_name == DEFAULT_MODELS[nome]


def test_nome_del_provider_case_insensitive():
    assert isinstance(get_provider("OpenAI"), OpenAIProvider)


def test_modello_esplicito_vince_sul_default():
    assert get_provider("openai", model_name="gpt-4o").model_name == "gpt-4o"


# --- Risoluzione della API key ---------------------------------------------------
def test_chiave_letta_dalla_variabile_dambiente(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-da-ambiente")
    assert get_provider("openai").api_key == "sk-da-ambiente"


def test_chiave_esplicita_vince_su_quella_dambiente(monkeypatch):
    # L'utente può incollare una chiave nella UI su una macchina che ne ha già
    # un'altra esportata: deve valere quella appena inserita.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-da-ambiente")
    assert get_provider("openai", api_key="sk-esplicita").api_key == "sk-esplicita"


@pytest.mark.parametrize("var", ["GOOGLE_API_KEY", "GEMINI_API_KEY"])
def test_gemini_accetta_entrambe_le_variabili(monkeypatch, var):
    # ENV_VAR è una TUPLA di alternative: entrambe sono in uso nella documentazione
    # Google, e chi ne esporta solo una deve funzionare comunque.
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv(var, "AIza-da-ambiente")
    assert GeminiProvider(model_name="gemini-2.0-flash").api_key == "AIza-da-ambiente"


def test_gemini_preferisce_google_api_key_quando_ci_sono_entrambe(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "primo")
    monkeypatch.setenv("GEMINI_API_KEY", "secondo")
    assert GeminiProvider(model_name="gemini-2.0-flash").api_key == "primo"


def test_provider_locale_non_cerca_chiavi(monkeypatch):
    # ENV_VAR = None (Ollama gira in locale): nessuna variabile deve essere letta.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-non-deve-essere-usata")
    assert OllamaProvider(model_name="qwen2.5:3b").api_key is None


def test_chiave_assente_resta_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_provider("openai").api_key is None


def test_name_espone_la_classe_concreta():
    # `name` finisce nei log e nel messaggio di ProviderError mostrato all'utente.
    assert get_provider("openai").name == "OpenAIProvider"


class _FlakyProvider(LLMProvider):
    """Provider finto: fallisce le prime `fails` volte, poi risponde."""

    def __init__(self, fails: int):
        super().__init__(model_name="fake")
        self._fails = fails
        self.calls = 0

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self.calls <= self._fails:
            raise RuntimeError("errore transitorio")
        return "ok"


def test_retry_riesce_dopo_fallimenti_transitori(monkeypatch):
    # 2 tentativi extra, niente attesa reale
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=2, retry_backoff=0.0))
    monkeypatch.setattr(base_mod.time, "sleep", lambda _s: None)

    p = _FlakyProvider(fails=2)
    assert p.generate("sys", "user") == "ok"
    assert p.calls == 3  # 1 iniziale + 2 retry


def test_retry_esaurito_rilancia(monkeypatch):
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=1, retry_backoff=0.0))
    monkeypatch.setattr(base_mod.time, "sleep", lambda _s: None)

    p = _FlakyProvider(fails=5)  # fallisce sempre entro i tentativi disponibili
    with pytest.raises(RuntimeError):
        p.generate("sys", "user")
    assert p.calls == 2  # 1 iniziale + 1 retry


# --- Ritentabilità: un 4xx non transitorio non deve bruciare i tentativi ---------
class _HTTPError(Exception):
    """Eccezione che imita quelle degli SDK: espone un codice HTTP."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _StatusProvider(LLMProvider):
    """Fallisce SEMPRE con un dato codice HTTP; conta le chiamate."""

    def __init__(self, status_code: int):
        super().__init__(model_name="fake")
        self._status_code = status_code
        self.calls = 0

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        raise _HTTPError(self._status_code)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_errore_client_non_viene_ritentato(monkeypatch, status):
    # Chiave errata, modello inesistente, richiesta malformata: ritentare identico
    # fallirebbe uguale sprecando quota. Una sola chiamata, poi rilancia.
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=3, retry_backoff=0.0))
    monkeypatch.setattr(base_mod.time, "sleep", lambda _s: None)

    p = _StatusProvider(status_code=status)
    with pytest.raises(_HTTPError):
        p.generate("sys", "user")
    assert p.calls == 1  # nessun retry


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_errore_transitorio_viene_ritentato(monkeypatch, status):
    # Rate-limit, timeout server e 5xx sono transitori: si esauriscono i tentativi.
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=2, retry_backoff=0.0))
    monkeypatch.setattr(base_mod.time, "sleep", lambda _s: None)

    p = _StatusProvider(status_code=status)
    with pytest.raises(_HTTPError):
        p.generate("sys", "user")
    assert p.calls == 3  # 1 iniziale + 2 retry


def test_errore_senza_codice_http_resta_ritentabile(monkeypatch):
    # Un errore di rete/timeout non porta risposta HTTP: va trattato come transitorio.
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=1, retry_backoff=0.0))
    monkeypatch.setattr(base_mod.time, "sleep", lambda _s: None)

    p = _FlakyProvider(fails=5)  # RuntimeError: nessuno status_code
    with pytest.raises(RuntimeError):
        p.generate("sys", "user")
    assert p.calls == 2  # ritentato come prima
