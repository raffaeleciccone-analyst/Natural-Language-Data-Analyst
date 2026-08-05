"""
Test dell'orchestrazione di un turno, SENZA rete e senza Streamlit.

Il provider è finto (restituisce codice deciso dal test) e il conteggio delle sue
chiamate è l'asserzione centrale: la politica di retry è una scelta di costo
(ogni tentativo è una chiamata all'LLM pagata), quindi va verificata sul numero
di chiamate, non solo sul risultato finale.
"""
import pandas as pd
import pytest

from nlda.agent import DataAgent
from nlda.config import Settings
from nlda.providers.base import LLMProvider
from nlda.results import ExecutionFailure, ExecutionSuccess
from nlda.service import AnalysisService


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
    monkeypatch.setattr("nlda.sandbox.runner.settings", Settings(sandbox_subprocess=False))


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


def test_nessun_retry_su_una_libreria_assente(sales_df: pd.DataFrame):
    """
    Una libreria che manca manca anche al secondo tentativo: rigenerare il codice
    e' spesa certa senza possibilita' di successo — tre chiamate al modello, sulla
    demo pagate col credito del manutentore. E' lo stesso errore di classificazione
    gia' costato caro sul rifiuto della sandbox e sul worker che non parte.
    """
    service, provider = _service(
        ["fig = px.scatter(df, x='Profit', y='Sales', trendline='ols')\nresult = df"])
    turn = service.answer("mostrami la correlazione con la linea di tendenza",
                          sales_df, explain=False)

    assert isinstance(turn.result, ExecutionFailure)
    assert turn.result.kind == "dependency"
    assert turn.result.retryable is False
    assert len(provider.calls) == 1, "nessuna correzione: il modulo mancherebbe ancora"


def test_i_tentativi_sono_limitati(sales_df: pd.DataFrame):
    service, provider = _service(["df['ColonnaInesistente'].sum()"], max_retries=2)
    turn = service.answer("totale", sales_df, explain=False)

    assert isinstance(turn.result, ExecutionFailure)
    assert turn.result.kind == "runtime"
    assert len(provider.calls) == 3  # generazione + 2 correzioni, poi si smette


# --- Provider irraggiungibile: nessun tentativo di correzione ------------------
class _ProviderRotto(LLMProvider):
    """Provider che fallisce sempre, come una rete giù o una chiave scaduta."""

    def __init__(self):
        super().__init__(model_name="rotto")
        self.calls = 0

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        raise ConnectionError("connessione rifiutata")


def test_provider_irraggiungibile_non_innesca_correzioni(monkeypatch, sales_df: pd.DataFrame):
    # Il provider ritenta già per conto suo (backoff su errori transitori): qui lo
    # azzeriamo per contare solo i tentativi decisi dal servizio.
    monkeypatch.setattr("nlda.providers.base.settings",
                        Settings(max_retries=0, retry_backoff=0.0))
    provider = _ProviderRotto()
    service = AnalysisService(DataAgent(provider=provider), max_retries=3)

    turn = service.answer("totale vendite", sales_df, explain=True)

    assert isinstance(turn.result, ExecutionFailure)
    assert turn.result.kind == "provider"
    # Nessuna riformulazione del prompt risolve una rete giù: si chiude subito.
    assert turn.result.retryable is False
    assert provider.calls == 1
    assert turn.explanation is None


def test_il_guasto_del_provider_e_leggibile(monkeypatch, sales_df: pd.DataFrame):
    monkeypatch.setattr("nlda.providers.base.settings",
                        Settings(max_retries=0, retry_backoff=0.0))
    service = AnalysisService(DataAgent(provider=_ProviderRotto()))
    turn = service.answer("totale", sales_df, explain=False)

    assert isinstance(turn.result, ExecutionFailure)
    assert "connessione rifiutata" in turn.result.message


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


# --- Feedback a step (on_step) -------------------------------------------------
def test_on_step_riporta_le_fasi_in_ordine(sales_df: pd.DataFrame):
    service, _ = _service(["df['Sales'].sum()", "Le vendite totali sono 840."])
    fasi: list[str] = []
    service.answer("totale vendite", sales_df, explain=True, on_step=fasi.append)

    assert any("Genero il codice" in f for f in fasi)
    assert any("Eseguo il codice" in f for f in fasi)
    assert any("spiegazione" in f.lower() for f in fasi)
    # L'ordine è quello del percorso: codice prima dell'esecuzione, spiegazione ultima.
    assert fasi.index(next(f for f in fasi if "Genero" in f)) < \
           fasi.index(next(f for f in fasi if "Eseguo" in f))


def test_on_step_segnala_le_correzioni(sales_df: pd.DataFrame):
    service, _ = _service([
        "df['ColonnaInesistente'].sum()",   # fallisce: innesca una correzione
        "df['Sales'].sum()",
    ])
    fasi: list[str] = []
    service.answer("totale", sales_df, explain=False, on_step=fasi.append)

    assert any("Correggo il codice" in f and "1/" in f for f in fasi)


def test_on_step_e_opzionale(sales_df: pd.DataFrame):
    # Senza on_step il turno funziona identico: il default è un no-op.
    service, _ = _service(["df['Sales'].sum()"])
    turn = service.answer("totale", sales_df, explain=False)
    assert isinstance(turn.result, ExecutionSuccess)


# --- La spiegazione che ripeterebbe non si paga --------------------------------
_RISPOSTA_ONESTA = ("# mappa: profitto -> NESSUNA\n"
                    "result = 'Il dataset non contiene una colonna del profitto.'")


def test_non_si_paga_una_spiegazione_che_ripete(sales_df: pd.DataFrame):
    """
    Misurato sulla demo: con la grandezza assente la stessa informazione compariva
    tre volte, e una delle tre costava una chiamata al modello. L'asserzione e' sul
    NUMERO di chiamate, non sull'assenza del testo: e' il costo il difetto.
    """
    service, provider = _service([_RISPOSTA_ONESTA])
    turn = service.answer("qual e' il profitto per regione?", sales_df, explain=True)

    assert turn.explanation is None
    assert len(provider.calls) == 1, "la seconda chiamata sarebbe stata la narrazione"


def test_un_numero_si_spiega_come_sempre(sales_df: pd.DataFrame):
    """Il difetto opposto — un'app che tace per risparmiare — sarebbe peggiore."""
    service, provider = _service(["df['Sales'].sum()", "Le vendite totali sono 700."])
    turn = service.answer("qual e' il totale delle vendite?", sales_df, explain=True)

    assert turn.explanation
    assert len(provider.calls) == 2, "generazione del codice + narrazione"
