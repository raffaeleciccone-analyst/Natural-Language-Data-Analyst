"""
Test dell'orchestrazione di un turno, SENZA rete e senza Streamlit.

Il provider è finto (restituisce codice deciso dal test) e il conteggio delle sue
chiamate è l'asserzione centrale: la politica di retry è una scelta di costo
(ogni tentativo è una chiamata all'LLM pagata), quindi va verificata sul numero
di chiamate, non solo sul risultato finale.
"""
import pandas as pd
import pytest

from core.agent import DataAgent
from core.config import Settings
from core.providers.base import LLMProvider
from core.results import ExecutionFailure, ExecutionSuccess
from core.service import AnalysisService


class FakeProvider(LLMProvider):
    """Provider di prova: restituisce le risposte previste, in ordine, e le conta."""

    def __init__(self, responses):
        super().__init__(model_name="fake")
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        # Esaurite le risposte previste, ripete l'ultima: così un retry di troppo
        # non fa fallire il test con un IndexError, ma si vede nel conteggio.
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


@pytest.fixture(autouse=True)
def sandbox_in_process(monkeypatch):
    """
    Esegue il codice in-process: qui si testa l'orchestrazione, non l'isolamento
    (coperto da test_executor_ipc). Evita di avviare un interprete per tentativo.
    """
    monkeypatch.setattr("core.executor.settings", Settings(sandbox_subprocess=False))


def _service(responses, max_retries: int = 3) -> tuple[AnalysisService, FakeProvider]:
    provider = FakeProvider(responses)
    return AnalysisService(DataAgent(provider=provider), max_retries=max_retries), provider


# --- Percorso felice -----------------------------------------------------------
def test_successo_al_primo_colpo(sales_df: pd.DataFrame):
    service, provider = _service(["df['Sales'].sum()"])
    turn = service.answer("qual è il totale delle vendite", sales_df, explain=False)

    assert isinstance(turn.result, ExecutionSuccess)
    assert turn.result.value == sales_df["Sales"].sum()
    assert turn.question == "qual è il totale delle vendite"
    assert len(provider.calls) == 1  # nessun retry, nessuna spiegazione


# --- Retry: solo quando può servire --------------------------------------------
def test_retry_su_errore_runtime_poi_riesce(sales_df: pd.DataFrame):
    service, provider = _service([
        "df['ColonnaInesistente'].sum()",   # esplode sui dati: correggibile
        "df['Sales'].sum()",
    ])
    turn = service.answer("totale vendite", sales_df, explain=False)

    assert isinstance(turn.result, ExecutionSuccess)
    assert turn.result.value == sales_df["Sales"].sum()
    assert turn.code == "df['Sales'].sum()"
    assert len(provider.calls) == 2  # generazione + una correzione


def test_nessun_retry_sul_rifiuto_di_sicurezza(sales_df: pd.DataFrame):
    # È il bug che il tipo tipizzato chiude: prima il messaggio "Errore di
    # sicurezza..." finiva nel retry come qualsiasi altro errore, e bruciava tre
    # chiamate all'LLM per rigenerare codice che la sandbox avrebbe ribloccato.
    service, provider = _service(["df.to_csv('/tmp/x.csv')"])
    turn = service.answer("esporta i dati", sales_df, explain=False)

    assert isinstance(turn.result, ExecutionFailure)
    assert turn.result.kind == "security"
    assert len(provider.calls) == 1


def test_i_tentativi_sono_limitati(sales_df: pd.DataFrame):
    service, provider = _service(["df['ColonnaInesistente'].sum()"], max_retries=2)
    turn = service.answer("totale", sales_df, explain=False)

    assert isinstance(turn.result, ExecutionFailure)
    assert turn.result.kind == "runtime"
    assert len(provider.calls) == 3  # generazione + 2 correzioni, poi si smette


# --- Spiegazione ---------------------------------------------------------------
def test_spiegazione_generata_solo_se_richiesta(sales_df: pd.DataFrame):
    service, provider = _service(["df['Sales'].sum()", "Le vendite totali sono 840."])
    turn = service.answer("totale vendite", sales_df, explain=True)

    assert turn.explanation == "Le vendite totali sono 840."
    assert len(provider.calls) == 2  # codice + narrativa


def test_nessuna_spiegazione_con_explain_false(sales_df: pd.DataFrame):
    service, provider = _service(["df['Sales'].sum()"])
    turn = service.answer("totale vendite", sales_df, explain=False)

    assert turn.explanation is None
    assert len(provider.calls) == 1


def test_nessuna_spiegazione_su_un_fallimento(sales_df: pd.DataFrame):
    # Non c'è nulla da interpretare: spiegare un errore costerebbe una chiamata inutile.
    service, provider = _service(["df.to_csv('/tmp/x.csv')"])
    turn = service.answer("esporta i dati", sales_df, explain=True)

    assert turn.explanation is None
    assert len(provider.calls) == 1


def test_l_unita_di_misura_arriva_al_modello(sales_df: pd.DataFrame):
    service, provider = _service(["df['Sales'].sum()", "Il totale è 840 €."])
    service.answer("totale vendite", sales_df, explain=True, unit="€")

    prompt_narrativa = provider.calls[1][1]
    assert "€" in prompt_narrativa
