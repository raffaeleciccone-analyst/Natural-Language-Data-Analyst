"""Test della config (lettura da env) e del retry/backoff comune dei provider."""
import pytest

import nlda.providers.base as base_mod
from nlda.config import Settings
from nlda.providers.base import LLMProvider


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


def test_settings_env_malformata_usa_default(monkeypatch):
    monkeypatch.setenv("EXEC_TIMEOUT", "non-un-numero")
    assert Settings.from_env().exec_timeout == 12


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
