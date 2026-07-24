"""
Esportazione della conversazione in Markdown. Funzioni pure: si testano senza
Streamlit, verificando che domanda, risultato, spiegazione e codice finiscano nel
documento, e che i casi limite (fallimento, tabella lunga, pipe nelle celle) siano
gestiti senza rompere il Markdown.
"""
import pandas as pd

from nlda.export import conversation_to_markdown, turn_to_markdown
from nlda.results import EXECUTED_OK, ExecutionFailure, ExecutionSuccess
from nlda.service import Turn


def _turn_ok(question, value, *, fig=None, explanation=None, code="df['S'].sum()"):
    return Turn(question=question, code=code,
                result=ExecutionSuccess(fig=fig, value=value, summary=""),
                explanation=explanation)


# --- Un singolo turno ------------------------------------------------------------
def test_turno_scalare_contiene_domanda_risultato_e_codice():
    md = turn_to_markdown(_turn_ok("qual è il totale?", 840))
    assert "## qual è il totale?" in md
    assert "**Risultato:**" in md
    assert "840" in md
    assert "```python" in md and "df['S'].sum()" in md


def test_turno_include_la_spiegazione_quando_c_e():
    md = turn_to_markdown(_turn_ok("q", 1, explanation="Le vendite crescono."))
    assert "Le vendite crescono." in md


def test_turno_con_dataframe_diventa_tabella():
    df = pd.DataFrame({"Regione": ["Nord", "Sud"], "Vendite": [100, 200]})
    md = turn_to_markdown(_turn_ok("vendite per regione", df))
    assert "| Regione | Vendite |" in md
    assert "| --- | --- |" in md
    assert "| Nord | 100 |" in md


def test_turno_fallito_riporta_il_messaggio_non_il_codice_come_risultato():
    turn = Turn(question="esporta", code="df.to_csv('x')",
                result=ExecutionFailure("security", "Errore di sicurezza: I/O vietato.", "x"))
    md = turn_to_markdown(turn)
    assert "⚠️" in md and "Errore di sicurezza" in md
    assert "**Risultato:**" not in md   # un fallimento non ha un "risultato"


def test_turno_segnala_il_grafico():
    md = turn_to_markdown(_turn_ok("mostrami le vendite", 1, fig=object()))
    assert "grafico" in md.lower()


def test_sentinella_eseguito_senza_valore_non_finisce_nell_export():
    # Codice eseguito ma senza un valore da mostrare: la UI non stampa nulla, e
    # l'export deve fare lo stesso invece di scrivere la frase segnaposto.
    md = turn_to_markdown(_turn_ok("assegna e basta", EXECUTED_OK))
    assert EXECUTED_OK not in md
    assert "**Risultato:**" not in md
    assert "## assegna e basta" in md   # il turno c'è comunque (domanda + codice)


def test_grafico_senza_valore_resta_segnalato():
    # Anche quando il valore è la sentinella, un eventuale grafico va comunque citato.
    md = turn_to_markdown(_turn_ok("mostrami il trend", EXECUTED_OK, fig=object()))
    assert EXECUTED_OK not in md
    assert "grafico" in md.lower()


# --- Robustezza della tabella ----------------------------------------------------
def test_le_pipe_nelle_celle_sono_escapate():
    df = pd.DataFrame({"nome": ["a|b"], "n": [1]})
    md = turn_to_markdown(_turn_ok("q", df))
    assert "a\\|b" in md   # la pipe letterale non spezza la colonna


def test_tabella_lunga_viene_troncata_con_nota():
    df = pd.DataFrame({"n": range(120)})
    md = turn_to_markdown(_turn_ok("q", df))
    assert "mostrate 50 righe su 120" in md


# --- Intera conversazione --------------------------------------------------------
def test_conversazione_ha_testata_e_separa_i_turni():
    turns = [_turn_ok("prima?", 1), _turn_ok("seconda?", 2)]
    md = conversation_to_markdown(turns, dataset_label="File: vendite.csv")
    assert md.startswith("# Conversazione")
    assert "File: vendite.csv" in md
    assert "## prima?" in md and "## seconda?" in md
    assert md.count("\n---\n") >= 2   # testata + separatore fra i due turni


def test_conversazione_vuota_lo_dichiara():
    md = conversation_to_markdown([])
    assert "nessuna domanda" in md
