"""
Storico della chat: cap agli ultimi N turni (memoria) e render dei più vecchi in
un expander. Le sezioni di pagina vivono in `nlda.ui.pages` (importabile, nessuna
`st.*` a livello di modulo), quindi la logica di cap si testa direttamente; il
render con lo storico si verifica con AppTest, che esercita davvero il nesting
chat_message dentro expander.
"""
from streamlit.testing.v1 import AppTest

from nlda.results import ExecutionFailure
from nlda.service import Turn
from nlda.ui import pages


def _turno(n: int) -> Turn:
    return Turn(question=f"domanda {n}", code="df.sum()",
                result=ExecutionFailure("runtime", "errore finto", "df.sum()"))


# --- Cap dello storico (logica pura) ---------------------------------------------
def test_cap_storico_tiene_solo_gli_ultimi_n():
    turni = [_turno(i) for i in range(pages._MAX_TURNI + 5)]
    capped = pages._cap_storico(turni)
    assert len(capped) == pages._MAX_TURNI
    assert capped[0] is turni[5]      # i 5 più vecchi sono caduti
    assert capped[-1] is turni[-1]    # il più recente resta


def test_cap_storico_sotto_soglia_lascia_tutto():
    turni = [_turno(i) for i in range(3)]
    assert pages._cap_storico(turni) == turni


# --- Render dello storico: i vecchi vanno in un expander, senza eccezioni --------
_SCRIPT_CON_STORICO = """
import pandas as pd
import streamlit as st

from nlda.ui import pages
from nlda.results import ExecutionFailure
from nlda.service import Turn

if "messages" not in st.session_state:
    st.session_state.messages = [
        Turn(question=f"domanda {i}", code="df.sum()",
             result=ExecutionFailure("runtime", "errore finto", "df.sum()"))
        for i in range(5)
    ]

# service e limits non vengono toccati senza un invio del form.
pages.render_chat(service=None, df=pd.DataFrame({"a": [1]}),
                  limits=None, explain=False, unit="")
"""


def test_render_chat_mette_i_vecchi_in_un_expander_senza_errori():
    at = AppTest.from_string(_SCRIPT_CON_STORICO).run()
    assert not at.exception, at.exception   # ElementList vuota = nessuna eccezione
    # 5 turni, 3 in vista: 2 finiscono nell'expander "Conversazioni precedenti".
    assert len(at.expander) >= 1
    assert any("Conversazioni precedenti" in e.label for e in at.expander)


# --- Smoke dell'intera app: si disegna dopo lo split in nlda/ui/ -----------------
def test_app_completa_si_disegna_senza_errori(monkeypatch):
    # Verifica che main() orchestri l'intera pagina senza eccezioni dopo lo split
    # (sidebar → KPI → report → chat → sintesi). Si neutralizza la SOLA parte che
    # chiama il modello — la sintesi AI — altrimenti il test dipenderebbe da Ollama
    # (decine di secondi) e non sarebbe deterministico. Il resto è tutto Pandas/UI.
    monkeypatch.setattr("nlda.agent.DataAgent.overview", lambda self, *a, **k: "sintesi finta")
    at = AppTest.from_file("main.py", default_timeout=60).run()

    assert not at.exception, at.exception
    assert any(t.value == "Natural Language Data Analyst" for t in at.title)
    # Le sezioni chiave devono esserci: prova che sidebar, report e chat sono girati.
    testi = [s.value for s in at.subheader]
    assert "Report iniziale sui dati" in testi
    assert "Fai una domanda ai tuoi dati" in testi
    # Il dataset di default (Superstore) ha una colonna data: la sezione c'è.
    assert "Confronto tra periodi" in testi
    # Le sezioni di sidebar per filtro e join sono presenti (render_filter/render_join).
    assert "Filtro" in testi and "Unisci un secondo dataset" in testi


def test_streaming_di_una_risposta(monkeypatch):
    # Sostituisce le chiamate al modello con finti deterministici e verifica che,
    # inviando una domanda, la spiegazione si streammi nel turno (write_stream) e
    # compaia, senza eccezioni. In-process per non avviare un sottoprocesso.
    from nlda.config import Settings
    monkeypatch.setattr("nlda.agent.DataAgent.overview", lambda self, *a, **k: "")
    monkeypatch.setattr("nlda.agent.DataAgent.ask_code",
                        lambda self, q, df: "result = df['Sales'].sum()")
    monkeypatch.setattr("nlda.agent.DataAgent.explain_stream",
                        lambda self, q, s: iter(["La risposta ", "è pronta."]))
    monkeypatch.setattr("nlda.sandbox.runner.settings", Settings(sandbox_subprocess=False))

    at = AppTest.from_file("main.py", default_timeout=60).run()
    at.text_input(key="chat_q").set_value("qual è il totale?")
    next(b for b in at.button if b.label == "Invia").click().run()

    assert not at.exception, at.exception
    testo = " ".join(m.value for m in at.markdown if m.value)
    assert "La risposta è pronta." in testo


def test_il_filtro_restringe_la_pagina(monkeypatch):
    # Seleziona una colonna categoriale e un valore, e verifica che il badge "filtro
    # attivo" compaia senza eccezioni: prova che il filtro alimenta davvero la pagina.
    monkeypatch.setattr("nlda.agent.DataAgent.overview", lambda self, *a, **k: "")
    at = AppTest.from_file("main.py", default_timeout=60).run()

    flt = at.selectbox(key="flt_col")
    colonna = flt.options[1]                         # prima categoria reale (0 = "(nessun filtro)")
    flt.set_value(colonna).run()
    valori = at.multiselect(key="flt_vals").options
    at.multiselect(key="flt_vals").set_value([valori[0]]).run()

    assert not at.exception, at.exception
    assert any("filtro attivo" in c.value.lower() for c in at.caption)
