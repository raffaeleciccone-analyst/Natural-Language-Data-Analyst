"""
Infrastruttura di streaming della narrativa (effetto typewriter). Nessuna rete:
i provider sono finti. Si verifica il fallback (chi non fa streaming restituisce
comunque tutto in un blocco), la pulizia dei backtick, la tolleranza agli errori e
il costo dell'ultima chiamata.
"""
import pandas as pd

from nlda.agent import DataAgent
from nlda.api.streaming import trasmetti
from nlda.pricing import Usage
from nlda.providers.base import LLMProvider
from nlda.results import ExecutionSuccess
from nlda.service import AnalysisService, Turn


class _SenzaStream(LLMProvider):
    """Provider che implementa solo `_call`: usa lo stream di default (fallback)."""

    def __init__(self):
        super().__init__(model_name="m")

    def _call(self, s: str, u: str) -> str:
        return "risposta completa"


class _ConStream(LLMProvider):
    """Provider con streaming reale: cede blocchi e dichiara i token a fine stream."""

    def __init__(self, blocchi, model="gpt-4o-mini", local=False):
        super().__init__(model_name=model)
        self._blocchi = blocchi
        self.LOCAL = local

    def _call(self, s: str, u: str) -> str:
        return "".join(self._blocchi)

    def stream(self, s: str, u: str):
        yield from self._blocchi
        self._last_usage = Usage(input_tokens=1000, output_tokens=500)


class _StreamRotto(LLMProvider):
    def __init__(self):
        super().__init__(model_name="m")

    def _call(self, s: str, u: str) -> str:
        return ""

    def stream(self, s: str, u: str):
        raise RuntimeError("connessione persa")
        yield  # pragma: no cover — rende la funzione un generatore


# --- Fallback e costo (livello provider) ----------------------------------------
def test_stream_di_default_cede_tutta_la_risposta_in_un_blocco():
    assert list(_SenzaStream().stream("s", "u")) == ["risposta completa"]


def test_last_cost_locale_e_zero():
    p = _ConStream(["x"], model="qwen2.5:3b", local=True)
    list(p.stream("s", "u"))
    assert p.last_cost() == 0.0


def test_last_cost_cloud_dal_listino():
    p = _ConStream(["x"], model="gpt-4o-mini")
    list(p.stream("s", "u"))
    # gpt-4o-mini (0.15, 0.60)/1M: 1000*0.15/1e6 + 500*0.60/1e6 = 0.00045
    assert abs(p.last_cost() - 0.00045) < 1e-9


def test_last_cost_modello_ignoto_e_none():
    p = _ConStream(["x"], model="modello-mai-visto")
    list(p.stream("s", "u"))
    assert p.last_cost() is None


# --- Agente: explain_stream ------------------------------------------------------
def test_explain_stream_cede_i_blocchi_senza_toccarli():
    r"""
    I blocchi escono come li manda il modello: backtick e `$` compresi.

    Prima venivano ripuliti qui, e quella pulizia — regole del markdown di
    Streamlit — finiva anche nello stream che legge la chat React, che mostrava
    "216,36 \$" in chiaro. Ora si adatta al momento di disegnare
    (`ui_components.md_safe`), una volta per interfaccia.
    """
    agente = DataAgent(provider=_ConStream(["Le vendite ", "valgono `840` $."]))
    testo = "".join(agente.explain_stream("quanto?", "totale 840"))
    assert testo == "Le vendite valgono `840` $."

    from nlda.ui_components import md_safe
    assert md_safe(testo) == r"Le vendite valgono 840 \$."


def test_explain_stream_tollera_l_errore():
    agente = DataAgent(provider=_StreamRotto())
    testo = "".join(agente.explain_stream("q", "s"))
    assert testo.startswith("_(Impossibile generare la spiegazione")


# --- Service: stream_explanation -------------------------------------------------
def test_service_stream_explanation_cede_la_spiegazione():
    service = AnalysisService(DataAgent(provider=_ConStream(["Il totale ", "è 840."])))
    result = ExecutionSuccess(fig=None, value=840, summary="totale 840")
    testo = "".join(service.stream_explanation("quanto?", result, unit="€"))
    assert testo == "Il totale è 840."


def test_service_stream_explanation_passa_l_unita():
    # L'unità entra nel riepilogo dato al modello: qui il provider registra il prompt.
    class _Reg(LLMProvider):
        def __init__(self):
            super().__init__(model_name="m")
            self.visto = ""

        def _call(self, s, u):
            return ""

        def stream(self, s, u):
            self.visto = u
            yield "ok"

    reg = _Reg()
    service = AnalysisService(DataAgent(provider=reg))
    result = ExecutionSuccess(fig=None, value=1, summary="x")
    list(service.stream_explanation("q", result, unit="€"))
    assert "€" in reg.visto


def test_dataframe_come_summary_non_serve(sales_df: pd.DataFrame):
    # Guardia leggera: stream_explanation non esplode su un result con DataFrame.
    service = AnalysisService(DataAgent(provider=_ConStream(["ok"])))
    result = ExecutionSuccess(fig=None, value=sales_df, summary="")
    assert "".join(service.stream_explanation("q", result)) == "ok"


# --- Il DataFrame non resta in mano durante la narrazione ----------------------
def test_il_dataframe_si_libera_prima_dei_token():
    """
    Gli eventi `token` durano decine di secondi: tenere il DataFrame per tutto
    quel tempo, moltiplicato per gli stream simultanei, e' RAM vera — fino al
    tetto per dataset, per ogni stream aperto.

    ⚠️ Il servizio finto NON e' un `MagicMock`: quello registra gli argomenti
    delle chiamate, quindi terrebbe vivo il df da solo e il test passerebbe (o
    fallirebbe) per colpa dello strumento invece che del codice. Provato: con
    MagicMock il df risulta vivo anche quando il codice lo ha lasciato andare.
    """
    import gc
    import weakref

    turn = Turn(question="q", code="x",
                result=ExecutionSuccess(fig=None, value=3, summary=""))
    vivo: list[bool] = []

    class ServizioFinto:
        def answer(self, question, dati, **k):
            return turn

        def stream_explanation(self, *a, **k):
            gc.collect()
            vivo.append(riferimento() is not None)
            yield "testo"

    df = pd.DataFrame({"Regione": ["N", "S"], "Vendite": [1, 2]})
    riferimento = weakref.ref(df)
    eventi = trasmetti(ServizioFinto(), "q", df, unit="",
                       verso_json=lambda t, includi_spiegazione: {})
    del df                       # la rotta lascia la sua presa quando ritorna
    list(eventi)

    assert vivo == [False], "il DataFrame e' ancora in vita durante la narrazione"
