"""
Sandbox statica: decide se il codice generato dall'LLM e' eseguibile.

Qui non si esegue nulla — si ispeziona soltanto. La separazione conta: le regole
di sicurezza sono la parte che va letta con piu' attenzione, e tenerle in un file
di poche centinaia di righe che non fa altro le rende ispezionabili davvero.

Il principio e' il DEFAULT-DENY: `_ALLOWED_NODES` elenca i nodi ammessi e tutto
il resto e' rifiutato, compresi i costrutti che Python aggiungera' in futuro. Sui
nodi ammessi valgono poi regole mirate su attributi, nomi e chiavi.
"""
import ast
import re

from nlda.log import get_logger
from nlda.results import ExecutionFailure

log = get_logger(__name__)

# Whitelist di builtin sicuri: mai esporre __import__, open, eval, exec, ecc.
SAFE_BUILTINS = {
    "len": len, "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "sorted": sorted, "range": range, "list": list, "dict": dict, "set": set,
    "tuple": tuple, "str": str, "int": int, "float": float, "bool": bool,
    "enumerate": enumerate, "zip": zip, "print": print,
}
# Nomi vietati anche come identificatori semplici (difesa in profondità)
_FORBIDDEN_NAMES = {
    "eval", "exec", "open", "compile", "input", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "memoryview", "breakpoint", "help",
}

# Metodi/attributi pericolosi (I/O su file/rete, deserializzazione, esecuzione di
# espressioni, format-string, traversata dei moduli interni di pandas). Bloccati
# ovunque compaiano come attributo: chiude gli escape del tipo pd.read_pickle(...),
# df.to_csv('...'), df.eval(...), "{0.__class__}".format(df), pd.io.common...
# Attributi/metodi sempre vietati (non coperti dalla regola sul prefisso I/O sotto)
_DENY_ATTRS = {
    "eval", "query",                   # eseguono una stringa non ispezionabile dall'AST
    "format", "format_map",            # leak via "{0.__class__}".format(obj)
    "style",                           # Styler.to_html/.export scrivono file
    "io", "core", "compat",            # traversata dei moduli interni di pandas
    "ExcelWriter", "ExcelFile", "HDFStore",
}

# Ogni metodo che inizia con to_/read_/write_ scrive o legge file/rete ed è vietato,
# TRANNE questi convertitori 'puri' che restituiscono solo oggetti in memoria.
# (Chiude gli escape: df.to_html('path'), fig.write_html('path'), pd.read_pickle(url), ...)
_SAFE_CONVERTERS = {
    "to_frame", "to_list", "to_numpy", "to_dict", "to_records", "to_series",
    "to_datetime", "to_numeric", "to_timedelta", "to_period", "to_timestamp",
    "to_flat_index", "to_coo",
}
_IO_PREFIX = re.compile(r'^(to|read|write)_')

# Nota: apply/map/agg/pipe/transform NON sono bloccati: sono comuni e legittimi,
# e l'AST ispeziona comunque l'interno dei lambda passati, quindi non aprono escape.


class UnsafeCodeError(Exception):
    """Sollevata quando il codice generato contiene costruzioni non consentite."""
# --- Allowlist dei nodi AST ----------------------------------------------------
# Il default è NEGARE: tutto ciò che non compare qui viene rifiutato.
#
# Perché non una denylist: una lista di costrutti vietati descrive gli attacchi
# che conosciamo oggi e resta indietro a ogni evoluzione della sintassi (match,
# walrus, async, e qualunque cosa arrivi domani). Elencando invece la manciata di
# nodi che servono davvero a un'espressione Pandas, restano fuori per costruzione
# def/class/import/with/try/raise/global/yield/async e i costrutti futuri, senza
# doverli prevedere uno per uno.
#
# Nota sul contenimento delle risorse: 'for' e le comprehension restano ammessi
# perché sono iterazione legittima; un ciclo troppo lungo è contenuto dal TIMEOUT
# del sottoprocesso (e dal cap di memoria su POSIX), non dal validatore. 'while'
# resta escluso perché 'while True' è il caso degenere più comune e non serve mai
# a un'aggregazione Pandas.
_ALLOWED_NODES = {
    ast.Module, ast.Expr,
    # assegnazioni e flusso minimo
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.If, ast.For,
    ast.Pass, ast.Break, ast.Continue,
    # espressioni
    ast.Name, ast.Attribute, ast.Subscript, ast.Slice, ast.Call, ast.keyword,
    ast.Constant, ast.JoinedStr, ast.FormattedValue, ast.IfExp, ast.Starred,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.Lambda, ast.arguments, ast.arg,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
}

# Classi base ammesse in blocco: contesti (Load/Store) e operatori (+, ==, and, ~...).
# Sono nodi foglia senza potere espressivo proprio: elencarne le ~30 sottoclassi
# aggiungerebbe rumore senza aggiungere sicurezza.
_ALLOWED_NODE_BASES = (ast.expr_context, ast.operator, ast.unaryop, ast.cmpop, ast.boolop)

# Messaggi mirati per i costrutti che un modello prova più spesso: un errore
# comprensibile vale più di "costrutto 'ImportFrom' non consentito".
_DENIED_MESSAGES = {
    "Import": "gli import non sono consentiti",
    "ImportFrom": "gli import non sono consentiti",
    "While": "i cicli 'while' non sono consentiti",
    "FunctionDef": "la definizione di funzioni non è consentita",
    "AsyncFunctionDef": "la definizione di funzioni non è consentita",
    "ClassDef": "la definizione di classi non è consentita",
    "With": "il blocco 'with' non è consentito",
    "AsyncWith": "il blocco 'with' non è consentito",
    "Try": "il blocco 'try' non è consentito",
    "Raise": "'raise' non è consentito",
    "Global": "'global' non è consentito",
    "Nonlocal": "'nonlocal' non è consentito",
    "Delete": "'del' non è consentito",
    "Return": "'return' non è consentito fuori da una funzione",
}


def _node_is_allowed(node: ast.AST) -> bool:
    return type(node) in _ALLOWED_NODES or isinstance(node, _ALLOWED_NODE_BASES)


def _validate_ast(tree: ast.AST) -> None:
    """
    Sandbox statica: consente solo espressioni/assegnazioni Pandas 'innocue'.
    Rifiuta ogni nodo AST fuori dall'allowlist e, sui nodi ammessi, blocca
    attributi dunder/privati, nomi pericolosi, metodi di I/O o di esecuzione e
    chiavi dunder. Solleva UnsafeCodeError.
    """
    for node in ast.walk(tree):
        if not _node_is_allowed(node):
            name = type(node).__name__
            raise UnsafeCodeError(
                _DENIED_MESSAGES.get(name, f"il costrutto '{name}' non è consentito")
            )
        if isinstance(node, ast.Attribute):
            a = node.attr
            if a.startswith("_"):
                raise UnsafeCodeError(f"accesso all'attributo '{a}' non consentito")
            if a in _DENY_ATTRS:
                raise UnsafeCodeError(f"il metodo '{a}' non è consentito")
            if _IO_PREFIX.match(a) and a not in _SAFE_CONVERTERS:
                raise UnsafeCodeError(f"il metodo di I/O '{a}' non è consentito")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise UnsafeCodeError(f"uso di '{node.id}' non consentito")
        # blocca l'accesso a chiavi dunder tramite subscript: obj['__class__']
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith("__") and node.value.endswith("__"):
            raise UnsafeCodeError("accesso a chiavi dunder non consentito")


def _iter_stmts_in_order(body):
    """
    Percorre gli statement in ordine di ESECUZIONE, entrando nei corpi di
    if/for/with/try. `ast.walk` non garantisce l'ordine e il vecchio confronto sul
    `lineno` era un'euristica fragile: qui l'ordine è quello reale del sorgente.
    """
    for node in body:
        yield node
        for field in ("body", "orelse", "finalbody"):
            child = getattr(node, field, None)
            if isinstance(child, list):
                yield from _iter_stmts_in_order(child)


def _last_assigned_name(tree: ast.AST):
    """Nome dell'ultima variabile assegnata (anche dentro if/for), per recuperarne il valore."""
    name = None
    for node in _iter_stmts_in_order(getattr(tree, "body", [])):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name):
            name = node.target.id
    return name
def _parse_and_validate(code: str) -> "ast.Module | ExecutionFailure":
    """
    Parsifica il codice e applica la sandbox statica. Ritorna l'AST se il codice è
    valido ed eseguibile, altrimenti il fallimento con la sua causa.
    Unico punto di verità condiviso tra esecuzione in-process e pre-controllo.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return ExecutionFailure("syntax", f"Errore di sintassi nel codice generato: {e}", code)

    # Nessuno statement eseguibile: il modello ha risposto solo con commenti o con
    # un blocco vuoto. È un fallimento del codice generato (quindi correggibile con
    # un nuovo tentativo), non un successo senza risultato.
    if not tree.body:
        msg = code.lstrip("# ").strip() or "il modello non ha prodotto codice eseguibile"
        return ExecutionFailure("syntax", f"Errore: {msg}", code)

    # Sandbox statica: rifiuta le costruzioni pericolose prima di eseguire
    try:
        _validate_ast(tree)
    except UnsafeCodeError as e:
        # Traccia QUALE regola ha bocciato il codice: prezioso per capire i
        # tentativi di escape del modello (e per le regression di sicurezza).
        log.warning("Sandbox: codice rifiutato (%s) — %r", e, code[:200])
        return ExecutionFailure("security", f"Errore di sicurezza: {e}.", code)

    return tree
