"""
Esporta una conversazione in Markdown: domanda, risultato, spiegazione e il codice
generato. Funzioni PURE (nessuno Streamlit): la UI si limita a offrirne l'output in
un `st.download_button`, e così l'esportazione è testabile senza avviare l'app.

Il Markdown è la scelta giusta per il contenuto che conta — numeri e narrazione:
universale, leggibile ovunque, senza dipendenze. I grafici Plotly sono interattivi
e non si incorporano in Markdown: se ne dichiara la presenza, i dati che li generano
sono comunque nella tabella.
"""
from datetime import date

import pandas as pd

from nlda.results import EXECUTED_OK, ExecutionFailure, ExecutionSuccess
from nlda.service import Turn
from nlda.utils import fmt_num

_MAX_RIGHE_TABELLA = 50


def _cell(value) -> str:
    """Una cella di tabella: numeri leggibili, pipe escapate (non spezzano la tabella)."""
    if isinstance(value, float):
        value = fmt_num(value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def _dataframe_to_markdown(df: pd.DataFrame) -> str:
    """Tabella GitHub-flavored, costruita a mano (nessuna dipendenza da tabulate)."""
    troncata = len(df) > _MAX_RIGHE_TABELLA
    vista = df.head(_MAX_RIGHE_TABELLA)
    intestazioni = [_cell(c) for c in vista.columns]
    righe = ["| " + " | ".join(intestazioni) + " |",
             "| " + " | ".join(["---"] * len(intestazioni)) + " |"]
    for _, r in vista.iterrows():
        righe.append("| " + " | ".join(_cell(v) for v in r) + " |")
    testo = "\n".join(righe)
    if troncata:
        testo += f"\n\n_(mostrate {_MAX_RIGHE_TABELLA} righe su {len(df)})_"
    return testo


def _value_to_markdown(value) -> str:
    """
    Il risultato reso in Markdown: tabella per DataFrame/Series, testo per gli
    scalari, stringa vuota per il segnaposto "eseguito senza un valore da mostrare"
    — così l'export omette il blocco Risultato esattamente dove la UI non mostra
    nulla, invece di scrivere la frase sentinella in grassetto.
    """
    if isinstance(value, pd.DataFrame):
        return _dataframe_to_markdown(value)
    if isinstance(value, pd.Series):
        return _dataframe_to_markdown(value.to_frame())
    if isinstance(value, float):
        return f"**{fmt_num(value)}**"
    if value == EXECUTED_OK:
        return ""
    return f"**{value}**"


def turn_to_markdown(turn: Turn) -> str:
    """Un turno in Markdown: domanda, spiegazione, risultato e codice generato."""
    parti = [f"## {turn.question.strip()}"]

    if turn.explanation:
        parti.append(turn.explanation.strip())

    if isinstance(turn.result, ExecutionSuccess):
        corpo = _value_to_markdown(turn.result.value)
        if corpo:
            parti.append("**Risultato:**\n\n" + corpo)
        if turn.result.fig is not None:
            parti.append("_(la risposta include un grafico, visibile nell'app)_")
    elif isinstance(turn.result, ExecutionFailure):
        parti.append(f"> ⚠️ {turn.result.message}")

    if turn.code.strip():
        parti.append("**Codice generato:**\n\n```python\n" + turn.code.strip() + "\n```")

    return "\n\n".join(parti)


def conversation_to_markdown(turns: list[Turn], *, dataset_label: str = "") -> str:
    """
    L'intera conversazione in un documento Markdown, dal primo turno all'ultimo.

    `dataset_label` (opzionale) dice su quali dati verteva la sessione. Nessun altro
    metadato inventato: il provider/modello non viaggia nel `Turn`, quindi non si
    finge di conoscerlo.
    """
    testa = ["# Conversazione — Natural Language Data Analyst"]
    contesto = [f"Esportata il {date.today().isoformat()}"]
    if dataset_label:
        contesto.append(dataset_label)
    testa.append(" · ".join(contesto))

    if not turns:
        testa.append("_(nessuna domanda in questa sessione)_")
        return "\n\n".join(testa) + "\n"

    corpo = "\n\n---\n\n".join(turn_to_markdown(t) for t in turns)
    return "\n\n".join(testa) + "\n\n---\n\n" + corpo + "\n"
