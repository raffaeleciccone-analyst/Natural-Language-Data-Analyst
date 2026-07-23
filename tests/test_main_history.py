"""
Storico della chat: cap agli ultimi N turni (memoria) e render dei più vecchi in
un expander. main.py è importabile (set_page_config sta dentro configure_page, non
a livello di modulo), quindi la logica di cap si testa direttamente; il render con
lo storico si verifica con AppTest, che esercita davvero il nesting chat_message
dentro expander.
"""
from streamlit.testing.v1 import AppTest

import main
from nlda.results import ExecutionFailure
from nlda.service import Turn


def _turno(n: int) -> Turn:
    return Turn(question=f"domanda {n}", code="df.sum()",
                result=ExecutionFailure("runtime", "errore finto", "df.sum()"))


# --- Cap dello storico (logica pura) ---------------------------------------------
def test_cap_storico_tiene_solo_gli_ultimi_n():
    turni = [_turno(i) for i in range(main._MAX_TURNI + 5)]
    capped = main._cap_storico(turni)
    assert len(capped) == main._MAX_TURNI
    assert capped[0] is turni[5]      # i 5 più vecchi sono caduti
    assert capped[-1] is turni[-1]    # il più recente resta


def test_cap_storico_sotto_soglia_lascia_tutto():
    turni = [_turno(i) for i in range(3)]
    assert main._cap_storico(turni) == turni


# --- Render dello storico: i vecchi vanno in un expander, senza eccezioni --------
_SCRIPT_CON_STORICO = """
import pandas as pd
import streamlit as st

import main
from nlda.results import ExecutionFailure
from nlda.service import Turn

if "messages" not in st.session_state:
    st.session_state.messages = [
        Turn(question=f"domanda {i}", code="df.sum()",
             result=ExecutionFailure("runtime", "errore finto", "df.sum()"))
        for i in range(5)
    ]

# service e limits non vengono toccati senza un invio del form.
main.render_chat(service=None, df=pd.DataFrame({"a": [1]}),
                 limits=None, explain=False, unit="")
"""


def test_render_chat_mette_i_vecchi_in_un_expander_senza_errori():
    at = AppTest.from_string(_SCRIPT_CON_STORICO).run()
    assert not at.exception, at.exception   # ElementList vuota = nessuna eccezione
    # 5 turni, 3 in vista: 2 finiscono nell'expander "Conversazioni precedenti".
    assert len(at.expander) >= 1
    assert any("Conversazioni precedenti" in e.label for e in at.expander)
