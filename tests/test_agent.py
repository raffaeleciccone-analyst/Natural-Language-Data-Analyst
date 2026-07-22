"""
Test dell'agente SENZA rete: euristica grafico, wrapping, sanitizzazione del prompt.

Nessuna chiamata all'LLM: costruire DataAgent con il provider locale non contatta
niente (l'import del client è lazy, dentro _call). Qui testiamo la logica pura.
"""
import pandas as pd
import pytest

import nlda.providers.base as base_mod
from nlda.agent import DataAgent, _describe_schema
from nlda.config import Settings
from nlda.errors import ProviderError
from nlda.providers.base import LLMProvider


def _agent() -> DataAgent:
    return DataAgent(provider="ollama")  # nessuna API key, nessuna rete alla costruzione


def test_chart_intent_riconosce_grafico():
    a = _agent()
    wants, kind = a._chart_intent("mostrami un grafico delle vendite")
    assert wants is True
    assert kind == "bar"


def test_chart_intent_andamento_e_linea():
    a = _agent()
    wants, kind = a._chart_intent("qual è l'andamento nel tempo")
    assert wants is True
    assert kind == "line"


def test_chart_intent_domanda_scalare_niente_grafico():
    a = _agent()
    wants, _ = a._chart_intent("qual è il totale delle vendite")
    assert wants is False


def test_wrap_chart_conserva_i_dati_oltre_alla_figura():
    # Il risultato resta in `result`: se la figura non si può disegnare l'utente
    # vede comunque i numeri, invece di un errore.
    a = _agent()
    wrapped = a._wrap_chart("df.groupby('R')['S'].sum()", wants=True, kind="bar")
    assert wrapped == "result = df.groupby('R')['S'].sum()\nfig = try_chart(result, kind='bar')"


def test_wrap_chart_riusa_la_variabile_gia_assegnata():
    # È la forma che il prompt stesso insegna (regola 6), quindi la più frequente.
    # Il codice NON va inserito dentro una chiamata: `to_chart(result = df...,
    # kind='bar')` è sintassi valida e fallisce a runtime con un messaggio
    # incomprensibile, poi ritentato tre volte. Si aggiunge la riga della figura.
    codice = "result = df.groupby('R')['S'].sum()"
    assert _agent()._wrap_chart(codice, wants=True, kind="bar") == (
        codice + "\nfig = try_chart(result, kind='bar')")


def test_wrap_chart_riusa_lultima_variabile_di_codice_multiriga():
    codice = "agg = df.groupby('R')['S'].sum()\nresult = agg.head(5)"
    assert _agent()._wrap_chart(codice, wants=True, kind="bar") == (
        codice + "\nfig = try_chart(result, kind='bar')")


def test_wrap_chart_lascia_stare_il_codice_che_non_sa_gestire():
    # Non termina con un'assegnazione a un nome semplice: meglio nessun grafico
    # che codice rotto.
    codice = "for i in range(3):\n    x = i"
    assert _agent()._wrap_chart(codice, wants=True, kind="bar") == codice


# --- Le due fonti di verità sul grafico devono restare allineate ---------------
def test_le_parole_del_prompt_sono_tutte_riconosciute_dalleuristica():
    """
    La regola 4 del system prompt elenca le parole per cui il modello DEVE
    disegnare; `_CHART_WORDS` decide se avvolgere il risultato quando il modello
    non l'ha fatto. Se le due liste divergono, l'app promette un grafico che non
    arriva: è successo con "mostrami", che il README propone come esempio.
    """
    import re

    a = _agent()
    prompt = a._get_system_prompt(pd.DataFrame({"Region": ["N"], "Sales": [1]}))
    regola4 = prompt.split("\n4. ")[1].split("\n5. ")[0]
    parole = re.findall(r'"([a-zà-ù]+)"', regola4.split("DEVI")[0])

    assert parole, "regola 4 non trovata nel prompt: il test va aggiornato"
    mancanti = [p for p in parole if p not in a._CHART_WORDS]
    assert not mancanti, f"parole promesse dal prompt ma ignorate dall'euristica: {mancanti}"


@pytest.mark.parametrize("domanda", ["Mostrami le vendite per regione",
                                     "visualizza il fatturato",
                                     "mostra le vendite per categoria"])
def test_le_domande_proposte_allutente_chiedono_un_grafico(domanda):
    # Sono le formulazioni che README e placeholder suggeriscono: devono funzionare.
    assert _agent()._chart_intent(domanda)[0] is True


def test_wrap_chart_non_tocca_codice_con_figura():
    a = _agent()
    code = "fig = px.bar(df, x='R', y='S')"
    assert a._wrap_chart(code, wants=True, kind="bar") == code


def test_describe_schema_elenca_le_colonne():
    df = pd.DataFrame({"Region": ["N", "S"], "Sales": [1, 2]})
    schema = _describe_schema(df)
    assert "Region" in schema
    assert "Sales" in schema


class _ProviderRotto(LLMProvider):
    """Provider che fallisce sempre: imita un modello irraggiungibile."""

    def __init__(self):
        super().__init__(model_name="rotto")

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("connessione rifiutata")


class _ProviderFinto(LLMProvider):
    """Provider che risponde con un testo fisso, senza rete."""

    def __init__(self, risposta: str):
        super().__init__(model_name="finto")
        self.risposta = risposta

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        return self.risposta


@pytest.fixture(autouse=True)
def _niente_attese(monkeypatch):
    """Azzera il backoff: i retry del provider non devono rallentare la suite."""
    monkeypatch.setattr(base_mod, "settings", Settings(max_retries=0, retry_backoff=0.0))


@pytest.mark.parametrize("metodo, argomenti", [
    ("overview", ("profilo del dataset",)),
    ("explain", ("quanto vendo?", "totale 100")),
    ("executive_report", ("insight già calcolati",)),
])
def test_narrativa_fallita_non_propaga_ma_avvisa(metodo, argomenti):
    # Differenza deliberata rispetto a `_generate`: la narrativa è un complemento,
    # non il risultato. Se il modello non risponde l'utente deve comunque vedere i
    # numeri calcolati da Pandas, con un avviso al posto del commento.
    agente = DataAgent(provider=_ProviderRotto())
    testo = getattr(agente, metodo)(*argomenti)
    assert testo.startswith("_(Impossibile generare ") and testo.endswith(")_")
    assert "connessione rifiutata" in testo


def test_generazione_codice_fallita_solleva_providererror(sales_df: pd.DataFrame):
    # Il contrapposto: qui il guasto è definitivo e NON va mascherato da un
    # commento, altrimenti l'executor lo scambierebbe per codice da correggere.
    agente = DataAgent(provider=_ProviderRotto())
    with pytest.raises(ProviderError):
        agente.ask_code("qual è il totale?", sales_df)


def test_narrativa_ripulita_dai_backtick():
    # Alcuni modelli infilano backtick a caso: in Markdown diventano frammenti
    # monospace in mezzo alla frase.
    agente = DataAgent(provider=_ProviderFinto("  Le vendite valgono `1.000`.  "))
    assert agente.explain("quanto?", "1000") == "Le vendite valgono 1.000."
