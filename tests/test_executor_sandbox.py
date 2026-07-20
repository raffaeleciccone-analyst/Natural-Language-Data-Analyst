"""
Test della sandbox statica dell'executor — il cuore di sicurezza del progetto.

Questi test hanno un doppio valore: dimostrano che il validatore funziona E
fanno da REGRESSION di sicurezza. Ogni nuova regola di blocco dovrebbe aggiungere
qui una riga in `REJECTED`: se un domani una modifica riaprisse l'escape, il test
diventa rosso.
"""
import ast

import pandas as pd
import pytest

from core.executor import (
    _last_assigned_name,
    _parse_and_validate,
    _run_code,
    execute_pandas_code,
)


def _is_rejected(code: str) -> bool:
    """True se la sandbox statica rifiuta il codice (ritorna una stringa d'errore)."""
    return isinstance(_parse_and_validate(code), str)


# --- Codice PERICOLOSO: deve essere sempre rifiutato ---------------------------
REJECTED = [
    # import in ogni forma
    "import os",
    "from os import system",
    # esecuzione di stringhe non ispezionabili
    "eval('1+1')",
    "exec('x=1')",
    "df.eval('Sales > 1')",
    "df.query('Sales > 1')",
    # I/O su file/rete (prefissi to_/read_/write_)
    "pd.read_pickle('http://evil/x')",
    "df.to_csv('/etc/passwd')",
    "df.to_html('x.html')",
    "px.bar(df).write_html('x.html')",
    # leak via format-string
    "'{0.__class__}'.format(df)",
    # accesso a dunder / attributi privati
    "df.__class__",
    "df._mgr",
    "df['__class__']",
    # builtin pericolosi
    "open('x')",
    "__import__('os')",
    "getattr(df, 'x')",
    "globals()",
    # traversata dei moduli interni di pandas / Styler che scrive file
    "pd.io.common.get_handle('x', 'w')",
    "df.style",
    # cicli potenzialmente infiniti
    "while True:\n    x = 1",
]


@pytest.mark.parametrize("code", REJECTED)
def test_codice_pericoloso_rifiutato(code: str):
    assert _is_rejected(code), f"NON rifiutato (escape!): {code!r}"


# --- Codice LEGITTIMO: deve passare il validatore ------------------------------
ALLOWED = [
    "df['Sales'].sum()",
    "df.groupby('Region', as_index=False)['Sales'].sum()",
    "df['Sales'].mean() * 2",
    "df.describe()",
    "df.sort_values('Sales', ascending=False).head(5)",
    # convertitori 'puri' in memoria: consentiti nonostante il prefisso to_
    "df['Sales'].to_dict()",
    "df['Sales'].to_numpy()",
    "df.to_dict()",
    # assegnazioni multiple + risultato
    "risultato = df['Sales'].sum()",
]


@pytest.mark.parametrize("code", ALLOWED)
def test_codice_legittimo_accettato(code: str):
    parsed = _parse_and_validate(code)
    assert isinstance(parsed, ast.AST), f"legittimo ma rifiutato: {code!r}"


# --- _last_assigned_name: ordine reale di esecuzione ---------------------------
def test_ultimo_assegnato_semplice():
    tree = ast.parse("a = 1\nb = 2\nc = 3")
    assert _last_assigned_name(tree) == "c"


def test_ultimo_assegnato_dentro_if():
    # 'y' è l'ultima assegnazione in ordine di sorgente, anche se annidata in un if
    code = "x = 1\nif True:\n    y = 2"
    assert _last_assigned_name(ast.parse(code)) == "y"


def test_ultimo_assegnato_augassign():
    code = "x = 1\nx += 5"
    assert _last_assigned_name(ast.parse(code)) == "x"


# --- Esecuzione end-to-end -----------------------------------------------------
def test_run_code_espressione(sales_df: pd.DataFrame):
    res = _run_code("df['Sales'].sum()", sales_df)
    assert isinstance(res, dict)
    assert res["value"] == sales_df["Sales"].sum()


def test_run_code_multistatement_con_risultato(sales_df: pd.DataFrame):
    code = "tot = df['Sales'].sum()\nrisultato = tot * 2"
    res = _run_code(code, sales_df)
    assert res["value"] == sales_df["Sales"].sum() * 2


def test_run_code_errore_ritorna_stringa(sales_df: pd.DataFrame):
    res = _run_code("df['ColonnaInesistente'].sum()", sales_df)
    assert isinstance(res, str)
    assert res.startswith("Errore")


def test_execute_pandas_code_blocca_codice_pericoloso(sales_df: pd.DataFrame):
    # Percorso pubblico completo: il pre-controllo deve bloccare prima di eseguire.
    out = execute_pandas_code("df.to_csv('x.csv')", sales_df)
    assert isinstance(out, str)
    assert "sicurezza" in out.lower()
