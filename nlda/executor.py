import ast
import io
import json
import os
import pickle
import re
import subprocess
import sys
from typing import cast

import pandas as pd

from nlda.config import settings
from nlda.log import get_logger
from nlda.results import (
    FAILURE_KINDS,
    ExecutionFailure,
    ExecutionResult,
    ExecutionSuccess,
    FailureKind,
)
from nlda.utils import clean_code, fmt_num

log = get_logger(__name__)

# Esecuzione isolata in un sottoprocesso con timeout (chiude i DoS da loop/allocazioni
# e aggiunge una barriera di processo). I parametri vivono in nlda.config; qui restano
# alias comodi per leggibilità e per i test.
SANDBOX_SUBPROCESS = settings.sandbox_subprocess
EXEC_TIMEOUT = settings.exec_timeout  # secondi

# Import lazy di Plotly: se non è installato, i grafici falliscono con un
# messaggio chiaro ma il resto dell'app continua a funzionare.
try:
    import plotly.express as px
    import plotly.graph_objects as go
    _PLOTLY_OK = True
except ImportError:  # pragma: no cover
    px = go = None
    _PLOTLY_OK = False

# Palette categorica validata (colorblind-safe) — dataviz reference palette
CATEGORICAL_LIGHT = [
    "#2a78d6", "#008300", "#e87ba4", "#eda100",
    "#1baf7a", "#eb6834", "#4a3aa7", "#e34948",
]

# Colori di superficie/inchiostro dei grafici (coerenti con l'UI "Console")
_THEME = dict(surface="#ffffff", ink="#16191c", secondary="#59626b",
              grid="#e2e6e1", axis="#d3d8d1", colorway=CATEGORICAL_LIGHT)

# Tipografia dei grafici (allineata all'UI: Plex Sans + Plex Mono per i numeri)
_FONT_SANS = "'IBM Plex Sans', system-ui, -apple-system, sans-serif"
_FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"

# Whitelist di builtin sicuri: mai esporre __import__, open, eval, exec, ecc.
SAFE_BUILTINS = {
    "len": len, "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
    "sorted": sorted, "range": range, "list": list, "dict": dict, "set": set,
    "tuple": tuple, "str": str, "int": int, "float": float, "bool": bool,
    "enumerate": enumerate, "zip": zip, "print": print,
}


def is_plotly_figure(obj) -> bool:
    return (type(obj).__module__ or "").startswith("plotly.")


def _make_bars_readable(fig):
    """
    Rende leggibili i grafici a barre con etichette lunghe: converte in ORIZZONTALE
    (nomi sull'asse y) invece di lasciarli verticali con testo ruotato. Idempotente.
    Vale per qualsiasi figura, anche se il modello ha usato px.bar a modo suo.
    """
    for tr in fig.data:
        if getattr(tr, "type", None) != "bar" or getattr(tr, "orientation", None) == "h":
            continue
        xs = list(tr.x) if tr.x is not None else []
        if xs and all(isinstance(v, str) for v in xs) and max((len(v) for v in xs), default=0) > 16:
            tr.x, tr.y = tr.y, tr.x
            tr.orientation = "h"
    # Barre orizzontali: tronca le etichette lunghe (nome intero nell'hover), togli
    # i titoli degli assi (ridondanti: bastano etichette, scala e titolo del grafico)
    # e dai un'altezza proporzionata.
    hbars = [tr for tr in fig.data
             if getattr(tr, "type", None) == "bar" and getattr(tr, "orientation", None) == "h"]
    for tr in hbars:
        if tr.y is None or tr.customdata is not None:
            continue
        full = [str(v) for v in tr.y]
        if any(len(s) > 28 for s in full):
            tr.y = [(s[:28] + "…") if len(s) > 29 else s for s in full]
            tr.customdata = full
            tr.hovertemplate = "%{customdata}<br>%{x}<extra></extra>"
    if hbars:
        fig.layout.xaxis.title.text = ""
        fig.layout.yaxis.title.text = ""
        n = max((len(tr.y) for tr in hbars if tr.y is not None), default=0)
        if n:
            # altezza compatta e barre più piene (poco spazio sprecato)
            fig.update_layout(height=max(220, 38 * n + 70), bargap=0.18)


def apply_theme(fig):
    """Applica il tema visuale coerente (colori, griglia, tipografia) a una figura Plotly."""
    t = _THEME
    _make_bars_readable(fig)
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
                      tickfont=dict(color=t["secondary"], family=_FONT_MONO, size=11),
                      automargin=True)  # riserva spazio per le etichette (niente tagli)
    fig.update_xaxes(**axis_style)
    fig.update_yaxes(**axis_style)
    return fig


def _require_plotly():
    """Solleva un errore chiaro se Plotly non è disponibile."""
    if not _PLOTLY_OK:
        raise RuntimeError("Plotly non è installato (pip install plotly).")


def to_chart(res, kind: str = "bar"):
    """
    Costruisce una figura Plotly a partire da una Series/DataFrame aggregata.
    Usata come fallback quando il modello non produce direttamente una figura.
    """
    _require_plotly()

    if isinstance(res, pd.Series):
        data = res.reset_index()
    elif isinstance(res, pd.DataFrame):
        data = res.reset_index() if res.index.name is not None else res.copy()
    else:
        raise TypeError("Il risultato non è graficabile (serve una Series o un DataFrame).")

    if data.shape[1] < 2:
        raise ValueError("Servono almeno due colonne (una per x e una per y) per creare un grafico.")

    x, y = data.columns[0], data.columns[1]

    if kind == "line":
        return apply_theme(px.line(data, x=x, y=y, markers=True))

    # Barre: ordina e limita; se le etichette sono lunghe o numerose usa l'orizzontale
    # (nomi leggibili sull'asse y) invece di etichette ruotate e illeggibili.
    try:
        data = data.sort_values(y, ascending=False)
    except Exception:
        pass
    etichette = data[x].astype(str)
    lunghe = bool(len(etichette)) and etichette.str.len().max() > 16
    if lunghe or len(data) > 12:
        data = data.head(15).sort_values(y, ascending=True)  # orizzontale: max in alto
        fig = px.bar(data, x=y, y=x, orientation="h")
    else:
        fig = px.bar(data, x=x, y=y)
    return apply_theme(fig)


def corr_heatmap(corr):
    """Heatmap di una matrice di correlazione: scala divergente (blu↔rosso) con
    midpoint neutro a 0, valori annotati. Range fisso [-1, 1]."""
    _require_plotly()
    fig = px.imshow(corr, zmin=-1, zmax=1, text_auto=".2f", aspect="auto",
                    color_continuous_scale=["#2a78d6", "#eef1ee", "#e34948"])
    fig.update_coloraxes(colorbar_title="r", cmid=0)
    fig.update_traces(hovertemplate="%{x} – %{y}<br>r = %{z:.2f}<extra></extra>")
    fig = apply_theme(fig)
    try:
        n = corr.shape[1]
    except Exception:
        n = 0
    fig.update_layout(height=max(240, 46 * n + 90))
    return fig


def histogram(df, col, nbins: int = 40):
    """
    Distribuzione (istogramma) di una colonna numerica, adattivo al tipo di dati.

    Molti dataset economici (vendite, importi, prezzi) sono log-normali: tanti
    valori piccoli e pochi enormi. Su un asse lineare tutto si schiaccia in
    un'unica barra a sinistra con una scia illeggibile. La scelta della scala è
    automatica e sicura per qualsiasi dataset caricato:

    * dati tutti positivi e fortemente asimmetrici → istogramma in SCALA
      LOGARITMICA (bin uniformi su log10): la distribuzione si "raddrizza" in una
      campana leggibile e si vede dove si concentra davvero la massa dei valori;
    * altrimenti (presenza di zeri/negativi o distribuzione regolare) → scala
      LINEARE con vista fino al 99° percentile e nota sugli outlier esclusi.
    """
    _require_plotly()
    serie = pd.to_numeric(df[col], errors="coerce").dropna()
    if serie.empty:
        raise ValueError(f"La colonna '{col}' non contiene valori numerici.")

    mediana = float(serie.median())
    p_high = float(serie.quantile(0.99))
    # Log solo se ha senso: valori strettamente positivi (log10 richiede > 0) e
    # coda destra marcata (il 99° pct dista molte volte dalla mediana). L'euristica
    # è conservativa: dataset "normali" (età, punteggi…) restano in lineare.
    usa_log = float(serie.min()) > 0 and mediana > 0 and (p_high / mediana) > 8

    if usa_log:
        import numpy as np
        log_vals = np.log10(serie)
        fig = px.histogram(x=log_vals, nbins=nbins)
        # Etichette dell'asse alle potenze di 10, ma scritte in scala originale
        # (1, 10, 100, 1.000…): l'utente legge i valori veri, non gli esponenti.
        lo, hi = int(np.floor(log_vals.min())), int(np.ceil(log_vals.max()))
        tick = list(range(lo, hi + 1))
        fig.update_xaxes(tickvals=tick, ticktext=[fmt_num(10 ** t) for t in tick])
        fig.update_layout(bargap=0.05, yaxis_title="record", showlegend=False,
                          xaxis_title=f"{col} (scala logaritmica)")
        return apply_theme(fig)

    # Ripiego lineare: limita la vista al 99° percentile se c'è una coda di outlier.
    n_out = int((serie > p_high).sum())
    skewed = n_out > 0 and p_high > float(serie.min())
    dati = serie[serie <= p_high] if skewed else serie
    fig = px.histogram(dati, nbins=nbins)
    fig.update_layout(bargap=0.05, yaxis_title="record",
                      xaxis_title=str(col), showlegend=False)
    if skewed:
        fig.add_annotation(
            xref="paper", yref="paper", x=1, y=1.06, xanchor="right", yanchor="bottom",
            showarrow=False, align="right",
            text=(f"Vista fino al 99° percentile ({fmt_num(p_high)}) · "
                  f"{fmt_num(n_out)} outlier oltre soglia non mostrati"),
            font=dict(size=11, color=_THEME["secondary"]),
        )
    return apply_theme(fig)


def _fig_summary(fig, max_rows: int = 30) -> str:
    righe = []
    for trace in fig.data:
        nome = getattr(trace, "name", None) or "serie"
        x_attr = getattr(trace, "x", None)
        y_attr = getattr(trace, "y", None)
        xs = list(x_attr) if x_attr is not None else []
        ys = list(y_attr) if y_attr is not None else []
        coppie = ", ".join(f"{x}={y}" for x, y in list(zip(xs, ys, strict=False))[:max_rows])
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
    """
    Testo di riepilogo di un risultato per l'LLM. Il flusso reale passa sempre un
    ExecutionSuccess (il riepilogo è già calcolato nel worker); resta un ripiego
    per oggetti grezzi (Series/DataFrame/scalari) e per il vecchio dict.
    """
    if isinstance(result, ExecutionSuccess):
        return result.summary or _make_summary(result.fig, result.value, max_rows)
    if isinstance(result, dict):
        return result.get("summary") or _make_summary(result.get("fig"), result.get("value"), max_rows)
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
            nome = type(node).__name__
            raise UnsafeCodeError(
                _DENIED_MESSAGES.get(nome, f"il costrutto '{nome}' non è consentito")
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


def _run_code(code: str, df: pd.DataFrame) -> ExecutionResult:
    """
    Valida ed esegue il codice. È il cuore eseguito sia in-process sia nel
    sottoprocesso: ritorna sempre un esito tipizzato, mai una stringa d'errore.
    """
    parsed = _parse_and_validate(code)
    if isinstance(parsed, ExecutionFailure):
        return parsed
    tree = parsed

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
        return ExecutionSuccess(fig=fig, value=value, summary=_make_summary(fig, value))

    except Exception as e:
        return ExecutionFailure("runtime", f"Errore di esecuzione sul codice generato: {e}", code)


# --- Canale di ritorno dal worker: SOLO dati, mai oggetti ----------------------
# Il worker è il processo in cui gira il codice generato dall'LLM: va trattato come
# potenzialmente compromesso. Deserializzare un pickle proveniente da lì darebbe
# esecuzione di codice arbitrario NEL PROCESSO PADRE (pickle costruisce oggetti, non
# li legge soltanto), vanificando proprio la barriera di processo che la sandbox
# vuole erigere: i due livelli condividerebbero lo stesso destino.
# Per questo il canale worker -> padre trasporta esclusivamente JSON. La direzione
# opposta (padre -> worker) resta su pickle: lì la sorgente è fidata ed è l'unico
# modo di trasferire un DataFrame conservandone i dtype.

def _encode_value(value) -> dict:
    """Riduce il valore prodotto dal codice generato a una struttura JSON-compatibile."""
    if value is None:
        return {"kind": "none"}
    if isinstance(value, pd.DataFrame):
        return {"kind": "frame", "data": value.to_json(orient="split", date_format="iso")}
    if isinstance(value, pd.Series):
        name = value.name if isinstance(value.name, str) else None
        return {"kind": "series", "name": name,
                "data": value.to_json(orient="split", date_format="iso")}
    # Scalari numpy/pandas (np.int64, np.float64, ...): .item() li riporta al tipo
    # Python nativo. Va tentato PRIMA dell'isinstance: np.float64 eredita da float,
    # quindi passerebbe il controllo restando un oggetto numpy, e il payload
    # dipenderebbe dalla tolleranza di json verso le sottoclassi invece che dal
    # nostro contratto. I tipi nativi non hanno .item(), quindi non sono toccati.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except Exception:  # noqa: BLE001 — array non scalari e simili: si degrada a testo
            pass
    if isinstance(value, (bool, int, float, str)):
        return {"kind": "scalar", "data": value}
    # Tutto il resto degrada a testo: meglio perdere il tipo che riaprire un canale
    # capace di istanziare oggetti arbitrari nel processo padre.
    return {"kind": "text", "data": str(value)}


def _decode_value(payload):
    """Ricostruisce il valore dal payload JSON. Non istanzia mai tipi arbitrari."""
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    try:
        if kind == "frame":
            return pd.read_json(io.StringIO(payload["data"]), orient="split")
        if kind == "series":
            s = pd.read_json(io.StringIO(payload["data"]), orient="split", typ="series")
            return s.rename(payload.get("name")) if payload.get("name") else s
    except Exception as e:  # noqa: BLE001 — payload malformato: si degrada, non si rompe
        log.warning("Payload tabellare non decodificabile dal sottoprocesso: %s", e)
        return None
    if kind in ("scalar", "text"):
        return payload.get("data")
    return None


def serialize_result(result: ExecutionResult) -> dict:
    """
    Prepara l'esito per il trasferimento dal sottoprocesso, in forma JSON.
    Anche il fallimento viaggia strutturato: la CAUSA ('kind') deve sopravvivere
    al viaggio, altrimenti il padre dovrebbe reinferirla dal testo del messaggio.
    """
    if isinstance(result, ExecutionFailure):
        return {"kind": "err",
                "failure": {"kind": result.kind, "message": result.message, "code": result.code}}
    fig = result.fig
    return {"kind": "ok",
            "fig": fig.to_json() if fig is not None else None,
            "value": _encode_value(result.value),
            "summary": result.summary}


def _deserialize_failure(payload: dict) -> ExecutionFailure:
    """Ricostruisce il fallimento dal payload, senza fidarsi di ciò che contiene."""
    fail = payload.get("failure")
    if not isinstance(fail, dict):
        return ExecutionFailure("internal", "Errore sconosciuto dall'ambiente di esecuzione isolato.")
    raw = fail.get("kind")
    # Un 'kind' fuori dai valori previsti non deve diventare una causa arbitraria:
    # il worker è non fidato, quindi si degrada su 'internal'.
    kind: FailureKind = cast(FailureKind, raw) if raw in FAILURE_KINDS else "internal"
    return ExecutionFailure(kind, str(fail.get("message") or "Errore sconosciuto."),
                            str(fail.get("code") or ""))


def _deserialize_result(payload) -> ExecutionResult:
    if not isinstance(payload, dict):
        return ExecutionFailure("internal", "Errore: risultato non valido dal sottoprocesso.")
    if payload.get("kind") == "err":
        return _deserialize_failure(payload)
    fig = None
    if payload.get("fig"):
        import plotly.io as pio
        fig = apply_theme(pio.from_json(payload["fig"]))
    return ExecutionSuccess(fig=fig, value=_decode_value(payload.get("value")),
                            summary=str(payload.get("summary") or ""))


def _run_in_subprocess(code: str, df: pd.DataFrame, timeout: int) -> ExecutionResult:
    """Esegue il codice in un interprete separato con timeout. Solleva se non avviabile."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
    # Andata (padre -> worker): pickle, sorgente fidata e dtype del DataFrame preservati.
    payload = pickle.dumps((code, df))
    proc = subprocess.run(
        [sys.executable, "-m", "nlda._sandbox_worker"],
        input=payload, capture_output=True, cwd=root, env=env, timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout:
        err = (proc.stderr or b"").decode(errors="replace").strip()[-300:]
        # Il worker è morto: la causa dipende da CHI l'ha ucciso, e da questo
        # discende se ritentare abbia senso.
        #   - abbattuto DAL codice generato (segnale/OOM, MemoryError) -> 'runtime':
        #     rigenerarlo più leggero è un tentativo plausibile;
        #   - non partito affatto (import fallito, PYTHONPATH rotto, dipendenza
        #     mancante nell'ambiente del sottoprocesso) -> 'internal': è rotto il
        #     deploy, non il codice. Ritentare brucerebbe tre chiamate all'LLM per
        #     ogni domanda, senza alcuna possibilità di successo.
        # In assenza di prove si sceglie 'internal': l'errore silenzioso costoso è
        # il deploy rotto che continua a pagare token, non l'OOM che non ritenta.
        ucciso_dal_codice = proc.returncode < 0 or "MemoryError" in err
        if ucciso_dal_codice:
            return ExecutionFailure("runtime", "Errore: esecuzione terminata in modo anomalo "
                                    "(possibile esaurimento memoria). Prova una domanda "
                                    "che aggreghi meno dati.", code)
        log.error("Worker terminato senza risultato (returncode=%s): %s", proc.returncode, err)
        return ExecutionFailure(
            "internal", "Errore: l'ambiente di esecuzione isolato non ha prodotto un "
            "risultato. Se il problema persiste, l'installazione potrebbe essere incompleta.",
            code)
    # Ritorno (worker -> padre): JSON. Un payload malformato produce un errore
    # leggibile, MAI l'esecuzione di codice nel padre.
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        log.error("Payload non valido dal sottoprocesso: %s", e)
        return ExecutionFailure(
            "internal", "Errore: risultato non leggibile dall'ambiente di esecuzione isolato.", code)
    return _deserialize_result(data)


def execute_pandas_code(code_string: str, df: pd.DataFrame) -> ExecutionResult:
    code = clean_code(code_string)

    # Pre-controllo rapido (fail-fast, senza avviare un processo)
    parsed = _parse_and_validate(code)
    if isinstance(parsed, ExecutionFailure):
        return parsed

    # Esecuzione isolata in sottoprocesso (con timeout e cap memoria su POSIX).
    if settings.sandbox_subprocess:
        try:
            return _run_in_subprocess(code, df, settings.exec_timeout)
        except subprocess.TimeoutExpired:
            return ExecutionFailure(
                "timeout", f"Errore: esecuzione interrotta dopo {settings.exec_timeout}s "
                "(codice troppo lento o troppo pesante).", code)
        except Exception as e:
            # Il sottoprocesso non è avviabile. L'in-process NON ha timeout né
            # limite di memoria: degradare a esso indebolisce la sandbox. Per questo
            # è dietro un flag che il deploy pubblico tiene spento (fail-closed).
            if not settings.allow_inprocess_fallback:
                log.error("Sandbox subprocess non avviabile e fallback disattivato: %s", e)
                return ExecutionFailure(
                    "internal", "Errore: ambiente di esecuzione isolato non disponibile. "
                    "Per sicurezza l'esecuzione è stata bloccata.", code)
            log.warning("Sandbox subprocess non avviabile (%s): fallback in-process "
                        "SENZA timeout/limite memoria.", e)

    return _run_code(code, df)
