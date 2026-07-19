import ast
import os
import pickle
import re
import subprocess
import sys

import pandas as pd

from core.utils import clean_code

# Esecuzione isolata in un sottoprocesso con timeout (chiude i DoS da loop/allocazioni
# e aggiunge una barriera di processo). Metti a False per eseguire in-process (più
# veloce, per uso locale con file fidati). Il fallback in-process scatta comunque se
# il sottoprocesso non è avviabile.
SANDBOX_SUBPROCESS = True
EXEC_TIMEOUT = 12  # secondi

# Import lazy di Plotly: se non è installato, i grafici falliscono con un
# messaggio chiaro ma il resto dell'app continua a funzionare.
try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY_OK = True
except ImportError:  # pragma: no cover
    px = go = None
    _PLOTLY_OK = False

# Palette categoriche validate (colorblind-safe) — dataviz reference palette
CATEGORICAL_LIGHT = [
    "#2a78d6", "#008300", "#e87ba4", "#eda100",
    "#1baf7a", "#eb6834", "#4a3aa7", "#e34948",
]
CATEGORICAL_DARK = [
    "#3987e5", "#008300", "#d55181", "#c98500",
    "#199e70", "#d95926", "#9085e9", "#e66767",
]

# Colori di superficie/inchiostro per i due temi (coerenti con l'UI "Console")
_THEMES = {
    "light": dict(surface="#ffffff", ink="#16191c", secondary="#59626b",
                  grid="#e2e6e1", axis="#d3d8d1", colorway=CATEGORICAL_LIGHT),
    "dark": dict(surface="#1a1a19", ink="#ffffff", secondary="#c3c2b7",
                 grid="#2c2c2a", axis="#3a3a38", colorway=CATEGORICAL_DARK),
}

# Tipografia dei grafici (allineata all'UI: Plex Sans + Plex Mono per i numeri)
_FONT_SANS = "'IBM Plex Sans', system-ui, -apple-system, sans-serif"
_FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"

# Tema corrente dei grafici (impostato dall'app prima dell'esecuzione)
_DARK = False


def set_theme(dark: bool) -> None:
    """Imposta il tema (chiaro/scuro) usato dai grafici Plotly generati."""
    global _DARK
    _DARK = bool(dark)

# Whitelist di builtin sicuri: mai esporre __import__, open, eval, exec, ecc.
SAFE_BUILTINS = {
    "len": len, "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "sorted": sorted, "range": range, "list": list, "dict": dict, "set": set,
    "tuple": tuple, "str": str, "int": int, "float": float, "bool": bool,
    "enumerate": enumerate, "zip": zip, "print": print,
}


def is_plotly_figure(obj) -> bool:
    return (type(obj).__module__ or "").startswith("plotly.")


def apply_theme(fig):
    """Applica il tema visuale coerente (colori, griglia, tipografia) a una figura Plotly."""
    t = _THEMES["dark" if _DARK else "light"]
    fig.update_layout(
        colorway=t["colorway"],
        paper_bgcolor=t["surface"],
        plot_bgcolor=t["surface"],
        font=dict(color=t["ink"], family=_FONT_SANS, size=13),
        margin=dict(l=12, r=12, t=48, b=12),
        # Preserva il titolo esistente ma evita "undefined" quando non c'è testo
        title=dict(text=(fig.layout.title.text or ""), font=dict(size=16, color=t["ink"])),
        legend=dict(bgcolor="rgba(0,0,0,0)", title_text=""),
        hoverlabel=dict(font_size=13, font_family=_FONT_MONO),
    )
    axis_style = dict(gridcolor=t["grid"], zerolinecolor=t["axis"], linecolor=t["axis"],
                      tickfont=dict(color=t["secondary"], family=_FONT_MONO, size=11))
    fig.update_xaxes(**axis_style)
    fig.update_yaxes(**axis_style)
    return fig


def to_chart(res, kind: str = "bar"):
    """
    Costruisce una figura Plotly a partire da una Series/DataFrame aggregata.
    Usata come fallback quando il modello non produce direttamente una figura.
    """
    if not _PLOTLY_OK:
        raise RuntimeError("Plotly non è installato (pip install plotly).")

    if isinstance(res, pd.Series):
        data = res.reset_index()
    elif isinstance(res, pd.DataFrame):
        data = res.reset_index() if res.index.name is not None else res.copy()
    else:
        raise TypeError("Il risultato non è graficabile (serve una Series o un DataFrame).")

    if data.shape[1] < 2:
        raise ValueError("Servono almeno due colonne (una per x e una per y) per creare un grafico.")

    x, y = data.columns[0], data.columns[1]
    fig = px.line(data, x=x, y=y, markers=True) if kind == "line" else px.bar(data, x=x, y=y)
    return apply_theme(fig)


def _fig_summary(fig, max_rows: int = 30) -> str:
    righe = []
    for trace in fig.data:
        nome = getattr(trace, "name", None) or "serie"
        x_attr = getattr(trace, "x", None)
        y_attr = getattr(trace, "y", None)
        xs = list(x_attr) if x_attr is not None else []
        ys = list(y_attr) if y_attr is not None else []
        coppie = ", ".join(f"{x}={y}" for x, y in list(zip(xs, ys))[:max_rows])
        righe.append(f"{nome}: {coppie}")
    return "Dati del grafico -> " + " | ".join(righe)


def _obj_summary(obj, max_rows: int = 30) -> str:
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return obj.head(max_rows).to_string()
    return str(obj)


def _make_summary(fig, value, max_rows: int = 30) -> str:
    """Riepilogo testuale del risultato per l'LLM: preferisce i dati alla figura."""
    if value is not None and not (isinstance(value, str) and value == "Codice eseguito correttamente."):
        return _obj_summary(value, max_rows)
    if fig is not None:
        return _fig_summary(fig, max_rows)
    return "Nessun risultato."


def summarize_result(result, max_rows: int = 30) -> str:
    """Trasforma un risultato (grafico+dati, figura, DataFrame, scalare) in testo per l'LLM."""
    if isinstance(result, dict):  # nuovo formato: {"fig":..., "value":..., "summary":...}
        if result.get("summary"):
            return result["summary"]
        return _make_summary(result.get("fig"), result.get("value"), max_rows)
    if is_plotly_figure(result):
        return _fig_summary(result, max_rows)
    return _obj_summary(result, max_rows)


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


def _validate_ast(tree: ast.AST) -> None:
    """
    Sandbox statica: consente solo espressioni/assegnazioni Pandas 'innocue'.
    Blocca import, attributi dunder/privati, nomi pericolosi, metodi di I/O o
    di esecuzione, e chiavi dunder. Solleva UnsafeCodeError.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise UnsafeCodeError("gli import non sono consentiti")
        if isinstance(node, ast.While):
            raise UnsafeCodeError("i cicli 'while' non sono consentiti")
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


def _last_assigned_name(tree: ast.AST):
    """Nome dell'ultima variabile assegnata (anche dentro if/for), per recuperarne il valore."""
    name, best_line = None, -1
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and node.lineno >= best_line:
                    name, best_line = target.id, node.lineno
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name):
            if node.lineno >= best_line:
                name, best_line = node.target.id, node.lineno
    return name


def _run_code(code: str, df: pd.DataFrame):
    """
    Parsifica, valida ed esegue il codice. Ritorna il risultato (valore/figura) o
    una stringa d'errore. È il cuore eseguito sia in-process sia nel sottoprocesso.
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"Errore di sintassi nel codice generato: {e} \nCodice tentato: {code}"

    # Nessuno statement eseguibile (es. il provider ha restituito solo un commento
    # "# Errore di comunicazione..."): è un fallimento, non un successo.
    if not tree.body:
        msg = code.lstrip("# ").strip() or "il modello non ha prodotto codice eseguibile"
        return f"Errore: {msg}"

    # Sandbox statica: rifiuta le costruzioni pericolose prima di eseguire
    try:
        _validate_ast(tree)
    except UnsafeCodeError as e:
        return f"Errore di sicurezza: {e}. \nCodice tentato: {code}"

    # Contesto isolato per l'esecuzione (builtin ridotti al minimo; niente 'st')
    safe_globals = {"__builtins__": SAFE_BUILTINS}
    local_context = {"df": df, "pd": pd, "px": px, "go": go, "to_chart": to_chart}

    try:
        fig = None
        value = None

        # Caso 1: singola espressione pura (es. df['Sales'].sum() o px.bar(...))
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
            v = eval(code, safe_globals, local_context)
            if is_plotly_figure(v):
                fig = apply_theme(v)
            else:
                value = v
        else:
            # Caso 2: statement -> exec. Può produrre CONTEMPORANEAMENTE un grafico
            # ('fig') e dei dati ('risultato').
            exec(code, safe_globals, local_context)
            f = local_context.get("fig")
            if is_plotly_figure(f):
                fig = apply_theme(f)
            for name in ("risultato", "result"):
                if local_context.get(name) is not None:
                    value = local_context[name]
                    break
            if value is None:
                last_name = _last_assigned_name(tree)
                if last_name and last_name in local_context \
                        and not is_plotly_figure(local_context[last_name]):
                    value = local_context[last_name]

        if fig is None and value is None:
            value = "Codice eseguito correttamente."

        # Il riepilogo testuale è calcolato QUI (figura reale) e viaggia col risultato.
        return {"fig": fig, "value": value, "summary": _make_summary(fig, value)}

    except Exception as e:
        return f"Errore di esecuzione sul codice generato: {e} \nCodice tentato: {code}"


def serialize_result(result):
    """Prepara il risultato per il trasferimento dal sottoprocesso (figura via JSON)."""
    if isinstance(result, str):  # stringa d'errore
        return {"kind": "err", "msg": result}
    fig = result.get("fig")
    val = result.get("value")
    if val is not None:
        try:
            pickle.dumps(val)
        except Exception:
            val = str(val)
    return {"kind": "ok", "fig": fig.to_json() if fig is not None else None,
            "value": val, "summary": result.get("summary")}


def _deserialize_result(payload):
    if not isinstance(payload, dict):
        return "Errore: risultato non valido dal sottoprocesso."
    if payload.get("kind") == "err":
        return payload.get("msg", "Errore sconosciuto.")
    fig = None
    if payload.get("fig"):
        import plotly.io as pio
        fig = apply_theme(pio.from_json(payload["fig"]))
    return {"fig": fig, "value": payload.get("value"), "summary": payload.get("summary")}


def _run_in_subprocess(code: str, df: pd.DataFrame, timeout: int):
    """Esegue il codice in un interprete separato con timeout. Solleva se non avviabile."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
    payload = pickle.dumps((code, df, _DARK))
    proc = subprocess.run(
        [sys.executable, "-m", "core._sandbox_worker"],
        input=payload, capture_output=True, cwd=root, env=env, timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout:
        err = (proc.stderr or b"").decode(errors="replace").strip()[-200:]
        return ("Errore: esecuzione terminata in modo anomalo "
                f"(possibile esaurimento memoria). {err}".strip())
    return _deserialize_result(pickle.loads(proc.stdout))


def execute_pandas_code(code_string: str, df: pd.DataFrame):
    code = clean_code(code_string)

    # Pre-controllo rapido (fail-fast, senza avviare un processo)
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"Errore di sintassi nel codice generato: {e} \nCodice tentato: {code}"
    if not tree.body:
        msg = code.lstrip("# ").strip() or "il modello non ha prodotto codice eseguibile"
        return f"Errore: {msg}"
    try:
        _validate_ast(tree)
    except UnsafeCodeError as e:
        return f"Errore di sicurezza: {e}. \nCodice tentato: {code}"

    # Esecuzione isolata in sottoprocesso (con timeout); fallback in-process.
    if SANDBOX_SUBPROCESS:
        try:
            return _run_in_subprocess(code, df, EXEC_TIMEOUT)
        except subprocess.TimeoutExpired:
            return (f"Errore: esecuzione interrotta dopo {EXEC_TIMEOUT}s "
                    "(codice troppo lento o troppo pesante).")
        except Exception:
            pass  # sottoprocesso non avviabile -> esecuzione in-process

    return _run_code(code, df)
