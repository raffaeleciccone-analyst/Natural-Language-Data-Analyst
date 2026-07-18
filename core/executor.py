import ast

import pandas as pd

from core.utils import clean_code

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
    "enumerate": enumerate, "zip": zip,
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
        title=dict(font=dict(size=16, color=t["ink"])),
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


def _finalize(value):
    """Se il valore è una figura Plotly le applica il tema; altrimenti lo restituisce così com'è."""
    if is_plotly_figure(value):
        return apply_theme(value)
    return value


def summarize_result(result, max_rows: int = 30) -> str:
    """Trasforma un risultato (figura/DataFrame/Series/scalare) in testo per l'LLM."""
    if is_plotly_figure(result):
        righe = []
        for trace in result.data:
            nome = getattr(trace, "name", None) or "serie"
            x_attr = getattr(trace, "x", None)
            y_attr = getattr(trace, "y", None)
            xs = list(x_attr) if x_attr is not None else []
            ys = list(y_attr) if y_attr is not None else []
            coppie = ", ".join(f"{x}={y}" for x, y in list(zip(xs, ys))[:max_rows])
            righe.append(f"{nome}: {coppie}")
        return "Dati del grafico -> " + " | ".join(righe)

    if isinstance(result, pd.DataFrame):
        return result.head(max_rows).to_string()
    if isinstance(result, pd.Series):
        return result.head(max_rows).to_string()
    return str(result)


# Nomi vietati anche come identificatori semplici (difesa in profondità)
_FORBIDDEN_NAMES = {
    "eval", "exec", "open", "compile", "input", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "memoryview", "breakpoint", "help",
}

# Metodi/attributi pericolosi (I/O su file/rete, deserializzazione, esecuzione di
# espressioni, format-string, traversata dei moduli interni di pandas). Bloccati
# ovunque compaiano come attributo: chiude gli escape del tipo pd.read_pickle(...),
# df.to_csv('...'), df.eval(...), "{0.__class__}".format(df), pd.io.common...
_DENY_ATTRS = {
    # esecuzione di espressioni da stringa (non ispezionabili dall'AST)
    "eval", "query",
    # deserializzazione
    "read_pickle", "to_pickle",
    # lettura da file/rete
    "read_csv", "read_table", "read_fwf", "read_excel", "read_json", "read_html",
    "read_xml", "read_sql", "read_sql_query", "read_sql_table", "read_parquet",
    "read_feather", "read_orc", "read_hdf", "read_stata", "read_sas", "read_spss",
    "read_gbq", "read_clipboard",
    # scrittura su file/rete/clipboard
    "to_csv", "to_excel", "to_json", "to_xml", "to_sql", "to_parquet", "to_feather",
    "to_orc", "to_hdf", "to_stata", "to_gbq", "to_clipboard",
    # oggetti I/O e format-string (leak via "{0.__class__}".format(obj))
    "ExcelWriter", "ExcelFile", "HDFStore", "format", "format_map",
    # traversata dei moduli interni di pandas
    "io", "core", "compat",
}

# Nota: apply/map/agg/pipe/transform NON sono bloccati: sono comuni e legittimi,
# e l'AST ispeziona comunque l'interno dei lambda passati, quindi non aprono escape.
# eval/query invece prendono una stringa che l'AST non vede -> restano bloccati.


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
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise UnsafeCodeError(f"accesso all'attributo '{node.attr}' non consentito")
            if node.attr in _DENY_ATTRS:
                raise UnsafeCodeError(f"il metodo '{node.attr}' non è consentito")
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


def execute_pandas_code(code_string: str, df: pd.DataFrame):
    code = clean_code(code_string)

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
    local_context = {
        "df": df,
        "pd": pd,
        "px": px,
        "go": go,
        "to_chart": to_chart,
    }

    try:
        # Caso 1: singola espressione pura (es. df['Sales'].sum() o px.bar(...))
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
            return _finalize(eval(code, safe_globals, local_context))

        # Caso 2: statement (assegnazioni, grafici, ecc.) -> exec
        exec(code, safe_globals, local_context)

        # Preferiamo restituire una figura, se ne è stata creata una (variabile 'fig')
        if "fig" in local_context and is_plotly_figure(local_context["fig"]):
            return apply_theme(local_context["fig"])

        # Altrimenti restituiamo l'ultima variabile assegnata (filtri, aggregazioni, ...)
        last_name = _last_assigned_name(tree)
        if last_name is not None and last_name in local_context:
            return _finalize(local_context[last_name])

        return "Codice eseguito correttamente."

    except Exception as e:
        return f"Errore di esecuzione sul codice generato: {e} \nCodice tentato: {code}"
