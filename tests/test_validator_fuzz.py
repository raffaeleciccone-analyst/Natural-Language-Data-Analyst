"""
Fuzz/property test del validatore statico della sandbox — il modulo più critico
per la sicurezza (#16). I 22 test fissi coprono idiomi noti; qui hypothesis genera
migliaia di espressioni, mescolando frammenti leciti e frammenti di fuga, per
cercare un input che il validatore ACCETTI pur contenendo un accesso pericoloso.

Invariante (l'oracolo `_indicatore_di_escape` è una SECONDA implementazione, con la
propria walk e le proprie liste, così un baco nel validatore non si nasconde dietro
lo stesso codice): **nessun codice accettato accede a `__`/import/I-O o a un nome
di fuga.** Se l'invariante regge su tutto ciò che hypothesis riesce a far accettare,
la superficie d'attacco statica è coperta ben oltre i casi scritti a mano.
"""
import ast
import re

import hypothesis.strategies as st
from hypothesis import given, settings

from nlda.results import ExecutionFailure
from nlda.sandbox.validator import _parse_and_validate

# --- Oracolo indipendente: cosa conta come "fuga" -------------------------------
_NOMI_DI_FUGA = {
    "eval", "exec", "open", "compile", "input", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "memoryview", "breakpoint", "help",
}
_IO = re.compile(r"^(to|read|write)_")
_CONVERTITORI_INNOCUI = {
    "to_frame", "to_list", "to_numpy", "to_dict", "to_records", "to_series",
    "to_datetime", "to_numeric", "to_timedelta", "to_period", "to_timestamp",
    "to_flat_index", "to_coo",
}


def _indicatore_di_escape(tree: ast.AST) -> str | None:
    """Descrive il primo accesso pericoloso trovato nell'AST, o None se pulito.
    Walk indipendente da quella del validatore: è l'oracolo del property test."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "import"
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                return f"attributo dunder/privato .{node.attr}"
            if _IO.match(node.attr) and node.attr not in _CONVERTITORI_INNOCUI:
                return f"metodo di I/O .{node.attr}"
        if isinstance(node, ast.Name) and node.id in _NOMI_DI_FUGA:
            return f"nome di fuga {node.id!r}"
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith("__") and node.value.endswith("__")):
            return f"chiave dunder {node.value!r}"
    return None


# --- Frammenti da cui comporre le espressioni -----------------------------------
# Foglie che il validatore deve ACCETTARE (idiomi Pandas legittimi).
_FOGLIE_SICURE = st.sampled_from([
    "df", "df['a']", "df['b']", "df.shape", "df.columns", "df.sum()",
    "df.groupby('a').sum()", "1", "2", "3.5", "'testo'", "df['a'].mean()",
])
# Foglie che DEVONO essere respinte: accesso a __/import/I-O/nomi di fuga.
_FOGLIE_PERICOLOSE = st.sampled_from([
    "__import__", "open", "eval", "exec", "globals", "getattr", "setattr", "vars",
    "().__class__", "[].__class__", "df.__dict__", "df.__class__", "type(df).__mro__",
    "df.to_csv", "df.to_pickle", "pd.read_pickle", "df.eval", "df.query",
    "df.style", "pd.io", "df.values.__array_interface__",
])


def _combinatore(foglie, chiavi):
    """Costruisce espressioni Python PARSABILI (somme, subscript, chiamate, metodi)
    a partire dalle foglie: così il ramo 'accettato' del validatore viene davvero
    esercitato, invece di finire quasi sempre in errore di sintassi."""
    return st.recursive(
        foglie,
        lambda e: st.one_of(
            st.builds("({} + {})".format, e, e),
            st.builds("{}.sum()".format, e),
            st.builds("{}[{!r}]".format, e, chiavi),
            st.builds("sum({})".format, e),
        ),
        max_leaves=5,
    )


_ESPR_SICURE = _combinatore(_FOGLIE_SICURE, st.sampled_from(["a", "b", "0"]))
_ESPR_MISTE = _combinatore(
    st.one_of(_FOGLIE_SICURE, _FOGLIE_PERICOLOSE),
    st.sampled_from(["a", "b", "0", "__class__", "__dict__"]),
)


# --- 1. L'invariante: nulla di accettato contiene una fuga ----------------------
@settings(max_examples=400)
@given(_ESPR_MISTE)
def test_nessun_codice_accettato_contiene_una_fuga(code: str):
    esito = _parse_and_validate(code)
    if isinstance(esito, ExecutionFailure):
        return  # respinto: l'invariante vale a vuoto
    # Accettato → `esito` è l'AST: l'oracolo indipendente non deve trovare nulla.
    indicatore = _indicatore_di_escape(esito)
    assert indicatore is None, f"ACCETTATO ma contiene {indicatore}: {code!r}"


# --- 2. Un frammento di fuga rende SEMPRE il codice respinto --------------------
@given(sicuro=_FOGLIE_SICURE, pericoloso=_FOGLIE_PERICOLOSE,
       op=st.sampled_from(["+", "-", "*", "=="]))
def test_un_frammento_pericoloso_viene_sempre_respinto(sicuro, pericoloso, op):
    code = f"({sicuro}) {op} ({pericoloso})"
    esito = _parse_and_validate(code)
    assert isinstance(esito, ExecutionFailure), f"NON respinto: {code!r}"
    assert esito.kind == "security"


# --- 3. Le composizioni sicure non vengono mai respinte per SICUREZZA -----------
# Controparte della #1: il validatore non deve diventare così severo da bocciare
# gli idiomi leciti come "non sicuri" (regressione che romperebbe l'app in
# silenzio). Un errore di SINTASSI è ammesso: il generatore compone anche codice
# non parsabile (es. '1.sum()'), e quello non è un giudizio di sicurezza.
@settings(max_examples=200)
@given(_ESPR_SICURE)
def test_le_composizioni_sicure_non_sono_mai_respinte_per_sicurezza(code: str):
    esito = _parse_and_validate(code)
    if isinstance(esito, ExecutionFailure):
        assert esito.kind != "security", f"RESPINTO come non sicuro un idioma lecito: {code!r}"
