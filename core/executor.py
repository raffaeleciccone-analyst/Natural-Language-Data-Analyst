import pandas as pd
import streamlit as st
import ast
import re

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

# Colori di superficie/inchiostro per i due temi
_THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", secondary="#52514e",
                  grid="#e1e0d9", axis="#c3c2b7", colorway=CATEGORICAL_LIGHT),
    "dark": dict(surface="#1a1a19", ink="#ffffff", secondary="#c3c2b7",
                 grid="#2c2c2a", axis="#383835", colorway=CATEGORICAL_DARK),
}

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
    return type(obj).__module__.startswith("plotly.")


def apply_theme(fig):
    """Applica il tema visuale coerente (colori, griglia, tipografia) a una figura Plotly."""
    t = _THEMES["dark" if _DARK else "light"]
    fig.update_layout(
        colorway=t["colorway"],
        paper_bgcolor=t["surface"],
        plot_bgcolor=t["surface"],
        font=dict(color=t["ink"],
                  family="system-ui, -apple-system, 'Segoe UI', sans-serif", size=13),
        margin=dict(l=12, r=12, t=48, b=12),
        title=dict(font=dict(size=16, color=t["ink"])),
        legend=dict(bgcolor="rgba(0,0,0,0)", title_text=""),
        hoverlabel=dict(font_size=13),
    )
    axis_style = dict(gridcolor=t["grid"], zerolinecolor=t["axis"],
                      linecolor=t["axis"], tickfont=dict(color=t["secondary"]))
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


def clean_generated_code(code_string: str) -> str:
    # Rimuove eventuali tag markdown che i modelli a volte inseriscono
    cleaned = re.sub(r'```(?:python)?\s*|\s*```', '', code_string)
    return cleaned.strip()


def _last_assigned_name(tree: ast.Module):
    """Ritorna il nome dell'ultima variabile assegnata (per recuperarne il valore dopo exec)."""
    name = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name):
            name = node.target.id
    return name


def execute_pandas_code(code_string: str, df: pd.DataFrame):
    code = clean_generated_code(code_string)

    # Contesto isolato per l'esecuzione (builtin ridotti al minimo indispensabile)
    safe_globals = {"__builtins__": SAFE_BUILTINS}
    local_context = {
        "df": df,
        "pd": pd,
        "st": st,
        "px": px,
        "go": go,
        "to_chart": to_chart,
    }

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"Errore di sintassi nel codice generato: {e} \nCodice tentato: {code}"

    try:
        # Caso 1: singola espressione pura (es. df['Sales'].sum() o px.bar(...))
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
            return _finalize(eval(code, safe_globals, local_context))

        # Caso 2: statement (assegnazioni, grafici, ecc.) -> exec
        exec(code, safe_globals, local_context)

        # Preferiamo restituire una figura, se ne è stata creata una (variabile 'fig')
        if "fig" in local_context and is_plotly_figure(local_context["fig"]):
            return apply_theme(local_context["fig"])

        # Se il codice usa direttamente Streamlit, è un effetto collaterale già renderizzato
        if "st." in code:
            return "Grafico generato con successo!"

        # Altrimenti restituiamo l'ultima variabile assegnata (filtri, aggregazioni, ...)
        last_name = _last_assigned_name(tree)
        if last_name is not None and last_name in local_context:
            return _finalize(local_context[last_name])

        return "Codice eseguito correttamente."

    except Exception as e:
        return f"Errore di esecuzione sul codice generato: {e} \nCodice tentato: {code}"
