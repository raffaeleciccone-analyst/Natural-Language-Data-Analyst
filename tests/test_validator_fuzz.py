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

Le prime tre proprietà coprono il layer STATICO (`_parse_and_validate`). La quarta
copre un layer diverso: la traversata dei sottomoduli (`px.np.f2py.subprocess`,
`px.data.os`) è fermata nel CONTESTO d'esecuzione (`_SafeModule` avvolge pd/px/go),
non dal validatore statico — quindi la si fuzza contro `_run_code`, con un oracolo
che deriva il pericolo dalla REALTÀ (risolve la catena sui moduli veri), non dalle
liste del validatore.
"""
import ast
import re
from types import ModuleType

import hypothesis.strategies as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from hypothesis import given, settings

from nlda.results import ExecutionFailure
from nlda.sandbox.runner import _run_code
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
# deadline=None: si misura la CORRETTEZZA, non la velocità. Senza, su una macchina
# di CI carica un singolo parse può superare i 200ms di default di hypothesis e
# far fallire il test in modo intermittente — il rosso che insegna a ignorare i rossi.
@settings(max_examples=400, deadline=None)
@given(_ESPR_MISTE)
def test_nessun_codice_accettato_contiene_una_fuga(code: str):
    esito = _parse_and_validate(code)
    if isinstance(esito, ExecutionFailure):
        return  # respinto: l'invariante vale a vuoto
    # Accettato → `esito` è l'AST: l'oracolo indipendente non deve trovare nulla.
    indicatore = _indicatore_di_escape(esito)
    assert indicatore is None, f"ACCETTATO ma contiene {indicatore}: {code!r}"


# --- 2. Un frammento di fuga rende SEMPRE il codice respinto --------------------
@settings(deadline=None)
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
@settings(max_examples=200, deadline=None)
@given(_ESPR_SICURE)
def test_le_composizioni_sicure_non_sono_mai_respinte_per_sicurezza(code: str):
    esito = _parse_and_validate(code)
    if isinstance(esito, ExecutionFailure):
        assert esito.kind != "security", f"RESPINTO come non sicuro un idioma lecito: {code!r}"


# --- 4. Traversata dei moduli: oracolo indipendente, verifica a runtime ---------
# L'oracolo NON replica le liste del validatore: risolve la catena di attributi sui
# moduli VERI e guarda se un hop, di fatto, È un modulo. È indipendenza reale — il
# pericolo deriva da ciò che gli oggetti SONO, non da una seconda copia della
# denylist. Il generatore mescola hop-sottomodulo (la porta verso os/subprocess) e
# hop di API legittima; la proprietà attraversa `_run_code`, dove vive la difesa.
_RADICI = {"pd": pd, "px": px, "go": go}
_DF_MINIMO = pd.DataFrame({"Region": ["A", "B"], "Sales": [1.0, 2.0]})

_HOP_MODULO = st.sampled_from(["np", "data", "io", "util", "testing", "api",
                               "plotting", "compat", "core", "os", "subprocess", "f2py"])
_HOP_SICURO = st.sampled_from(["bar", "line", "scatter", "DataFrame", "Series",
                               "concat", "Figure", "sum", "columns", "shape"])


@st.composite
def _catena(draw) -> str:
    radice = draw(st.sampled_from(list(_RADICI)))
    n = draw(st.integers(min_value=1, max_value=3))
    hop = [draw(st.one_of(_HOP_MODULO, _HOP_SICURO)) for _ in range(n)]
    return radice + "".join("." + h for h in hop)


def _raggiunge_un_sottomodulo(code: str) -> bool:
    """Oracolo indipendente: risolve la catena sui moduli VERI; True se un hop DOPO
    la radice è un modulo. Solo getattr, mai chiamate — nessun effetto collaterale."""
    parti = code.split(".")
    obj = _RADICI[parti[0]]
    for attr in parti[1:]:
        try:
            obj = getattr(obj, attr)
        except Exception:  # noqa: BLE001 — attributo assente: la catena non tocca un modulo
            return False
        if isinstance(obj, ModuleType):
            return True
    return False


@settings(max_examples=300, deadline=None)
@given(_catena())
def test_ogni_catena_che_raggiunge_un_modulo_e_bloccata(code: str):
    esito = _run_code(code, _DF_MINIMO)
    if _raggiunge_un_sottomodulo(code):
        # Tocca un sottomodulo → il namespace protetto DEVE fermarla, e come rifiuto
        # di SICUREZZA (non un errore qualsiasi che potrebbe innescare un retry).
        assert isinstance(esito, ExecutionFailure), f"traversata NON bloccata: {code!r}"
        assert esito.kind == "security", (code, esito.kind, esito.message)
