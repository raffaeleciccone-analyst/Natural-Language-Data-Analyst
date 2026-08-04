"""
Test dell'agente SENZA rete: euristica grafico, wrapping, sanitizzazione del prompt.

Nessuna chiamata all'LLM: costruire DataAgent con il provider locale non contatta
niente (l'import del client è lazy, dentro _call). Qui testiamo la logica pura.
"""
import pandas as pd
import pytest

import nlda.agent as agent_mod
import nlda.providers.base as base_mod
from nlda.agent import MAX_SCHEMA_COLS, DataAgent, _describe_schema
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


def test_wrap_chart_non_infila_i_commenti_dentro_l_assegnazione():
    """
    Con la regola 10 il modello scrive la mappa termine->colonna PRIMA del codice.
    `result = # mappa: ...` non e' Python, e l'AST non aiuta a scoprirlo: per lui
    i commenti non esistono, quindi l'espressione sembrava singola e valida. La
    dichiarazione resta davanti, dove il modello l'ha scritta e l'utente la legge.
    """
    codice = "# mappa: vendite -> S\ndf.groupby('R')['S'].sum()"
    wrapped = _agent()._wrap_chart(codice, wants=True, kind="bar")
    assert wrapped == ("# mappa: vendite -> S\n"
                       "result = df.groupby('R')['S'].sum()\n"
                       "fig = try_chart(result, kind='bar')")
    compile(wrapped, "<test>", "exec")   # deve essere Python valido, non solo simile


def test_wrap_chart_conserva_la_mappa_anche_riusando_la_variabile():
    codice = "# mappa: vendite -> S\nresult = df.groupby('R')['S'].sum()"
    wrapped = _agent()._wrap_chart(codice, wants=True, kind="bar")
    assert wrapped.startswith("# mappa: vendite -> S\n")
    compile(wrapped, "<test>", "exec")


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


class _ProviderNonInstallato(LLMProvider):
    """Provider la cui libreria non è installata: imita l'import lazy fallito
    (es. 'import ollama' senza il pacchetto). È il caso che un recruiter incontra
    aprendo la demo se il provider scelto non è disponibile."""

    def __init__(self):
        super().__init__(model_name="assente")

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        raise ModuleNotFoundError("No module named 'ollama'")


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
    # Il messaggio grezzo del provider NON finisce in pagina (resta nei log): è
    # rumore tecnico che farebbe sembrare l'app rotta a chi apre la demo.
    assert "connessione rifiutata" not in testo
    assert "non è al momento raggiungibile" in testo


def test_narrativa_non_svela_libreria_mancante():
    # Un ImportError ('No module named ollama') è un dettaglio implementativo: non
    # deve finire in pagina a far sembrare l'app rotta a un recruiter. Si mostra un
    # motivo comprensibile, e la traccia tecnica resta nei log.
    agente = DataAgent(provider=_ProviderNonInstallato())
    testo = agente.overview("profilo del dataset")
    assert "No module named" not in testo and "ModuleNotFoundError" not in testo
    assert "nessun modello LLM configurato" in testo


def test_generazione_codice_fallita_solleva_providererror(sales_df: pd.DataFrame):
    # Il contrapposto: qui il guasto è definitivo e NON va mascherato da un
    # commento, altrimenti l'executor lo scambierebbe per codice da correggere.
    agente = DataAgent(provider=_ProviderRotto())
    with pytest.raises(ProviderError):
        agente.ask_code("qual è il totale?", sales_df)


def test_la_narrativa_esce_com_e_dal_backend():
    """
    I backtick li toglie chi DISEGNA, non chi genera.

    Il test prima chiedeva il contrario: che `explain` li togliesse. Ma toglierli
    li' era adattare il testo al markdown di Streamlit dentro un modulo che serve
    anche il frontend React — dove i backtick sono legittimi e `Testo` li rende
    come codice inline. Ora la pulizia sta in `ui_components.senza_backtick`, e
    chi non importa Streamlit non puo' applicarla per sbaglio.
    """
    agente = DataAgent(provider=_ProviderFinto("  Le vendite valgono `1.000`.  "))
    assert agente.explain("quanto?", "1000") == "Le vendite valgono `1.000`."

    from nlda.ui_components import senza_backtick
    assert senza_backtick("Le vendite valgono `1.000`.") == "Le vendite valgono 1.000."


# --- #29: schema costruito una volta e con un tetto sulle colonne ----------------
def test_schema_costruito_una_volta_per_dataset(monkeypatch, sales_df):
    # _get_system_prompt è chiamato a ogni domanda E a ogni retry: sullo stesso df
    # lo schema non va ricalcolato (dropna().unique() per colonna costa).
    chiamate = {"n": 0}
    reale = agent_mod._describe_schema
    monkeypatch.setattr(agent_mod, "_describe_schema",
                        lambda df: (chiamate.__setitem__("n", chiamate["n"] + 1), reale(df))[1])
    a = _agent()
    a._get_system_prompt(sales_df)
    a._get_system_prompt(sales_df)
    assert chiamate["n"] == 1  # seconda volta: cache


def test_schema_ricalcolato_se_cambia_il_dataset(monkeypatch, sales_df):
    chiamate = {"n": 0}
    reale = agent_mod._describe_schema
    monkeypatch.setattr(agent_mod, "_describe_schema",
                        lambda df: (chiamate.__setitem__("n", chiamate["n"] + 1), reale(df))[1])
    a = _agent()
    a._get_system_prompt(sales_df)
    a._get_system_prompt(pd.DataFrame({"tutt'altro": [1, 2], "schema": ["x", "y"]}))
    assert chiamate["n"] == 2  # colonne diverse: firma diversa, si ricalcola


def test_schema_taglia_le_colonne_oltre_il_tetto():
    largo = pd.DataFrame({f"c{i}": [1] for i in range(MAX_SCHEMA_COLS + 5)})
    schema = _describe_schema(largo)
    assert "'c0'" in schema
    assert f"'c{MAX_SCHEMA_COLS}'" not in schema          # oltre il tetto: fuori
    assert f"altre {5} colonne" in schema                 # omissione dichiarata


def test_schema_sotto_il_tetto_elenca_tutto():
    stretto = pd.DataFrame({"a": [1], "b": ["x"]})
    schema = _describe_schema(stretto)
    assert "'a'" in schema and "'b'" in schema
    assert "colonne, non elencate" not in schema


# --- Il backend non conosce l'interfaccia --------------------------------------
def test_lo_stream_non_adatta_il_testo_a_streamlit():
    r"""
    `md_safe` neutralizza i `$` perche' una COPPIA e' un delimitatore LaTeX nel
    markdown di Streamlit. Veniva applicato dentro `agent.py`, cioe' nel backend
    che serve anche il frontend React: quei `\$` arrivavano alla chat React, che
    non li interpreta, e l'utente leggeva "216,36 \$" al primo messaggio.

    Il backend rende il testo del modello com'e'. Chi lo disegna decide.
    """
    from unittest.mock import MagicMock

    from nlda.agent import DataAgent

    agente = DataAgent.__new__(DataAgent)
    agente.provider = MagicMock()
    agente.provider.stream.return_value = ["costa 216,36 $ ", "in `Region` West"]
    agente.provider._last_usage = MagicMock(total_tokens=1)
    agente._explain_prompts = lambda q, r: ("s", "u")

    testo = "".join(agente.explain_stream("q", "r"))
    assert r"\$" not in testo, "l'escape di Streamlit non deve uscire dal backend"
    assert "`" in testo, "nemmeno i backtick vanno tolti qui"
    assert testo == "costa 216,36 $ in `Region` West"


def test_la_narrativa_non_stream_esce_intatta():
    """Stessa regola per il percorso senza streaming, che alimenta /overview,
    /executive-report e la risposta salvata."""
    from unittest.mock import MagicMock

    from nlda.agent import DataAgent

    agente = DataAgent.__new__(DataAgent)
    agente.provider = MagicMock()
    agente.provider.generate.return_value = "  totale 100 $ in `A`  "
    assert agente._narrate("s", "u", "x") == "totale 100 $ in `A`"


def test_i_due_adattamenti_di_streamlit_sono_distinti():
    r"""
    `md_safe` serve a `st.markdown`; il riquadro HTML vuole solo i backtick via,
    perche' un `\$` ci comparirebbe col backslash in chiaro. Erano una funzione
    sola, e il riquadro riceveva un escape che non sa disfare.
    """
    from nlda.ui_components import md_safe, senza_backtick

    assert md_safe("100 $ e `x`") == r"100 \$ e x"
    assert senza_backtick("100 $ e `x`") == "100 $ e x"
