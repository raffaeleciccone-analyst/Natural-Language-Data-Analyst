"""
Costruzione delle figure Plotly e loro aspetto.

Estratto da `executor.py`, che faceva tre cose in un file solo: validare il
codice generato, eseguirlo e disegnare grafici. Sono tre ragioni di cambiamento
diverse, e la prova che la divisione mancasse era che `ui_components.py` — cioe'
l'interfaccia — importava `apply_theme` dal modulo che contiene la sandbox.

Questo modulo non sa nulla di sandbox ne' di esecuzione: prende dati e
restituisce figure. La dipendenza va in una direzione sola, da chi esegue a chi
disegna.
"""
import pandas as pd

from nlda.log import get_logger
from nlda.utils import fmt_num

log = get_logger(__name__)

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

# Colori di superficie/inchiostro dei grafici (coerenti con l'UI "strumento": toni
# freddi, accento teal in testa alla colorway così le barre a serie singola sono teal;
# la palette categorica validata resta per i grafici multi-serie).
_THEME = dict(surface="#ffffff", ink="#1b1f24", secondary="#586471",
              grid="#e4e8ec", axis="#d5dbe1",
              colorway=["#0d8a7d", *CATEGORICAL_LIGHT])

# Colore delle serie SINGOLE (barre/linea/istogramma). Va passato alla CREAZIONE
# della figura: Plotly Express cuoce nella traccia il proprio colore di default e
# non rispetta layout.colorway impostato dopo — quindi si forza qui.
_BAR = _THEME["colorway"][0]

# Tipografia dei grafici (allineata all'UI: Plex Sans + Plex Mono per i numeri)
_FONT_SANS = "'IBM Plex Sans', system-ui, -apple-system, sans-serif"
_FONT_MONO = "'IBM Plex Mono', ui-monospace, monospace"
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


_TINTA_UNITA = {"bar", "scatter", "scattergl", "histogram", "box", "violin", "funnel"}


def _recolor_traces(fig, colorway):
    """
    Riassegna i colori delle tracce dalla colorway del tema.

    Plotly Express "cuoce" il colore nella traccia alla creazione e IGNORA
    `layout.colorway` impostato dopo. I grafici del report passano da `to_chart`,
    che il teal lo forza con `color_discrete_sequence`; ma quelli che l'AGENTE
    genera con `px.bar` diretto (senza quel parametro) nascono del blu di default.
    Risultato: barre blu nella chat, teal nel report. Qui si uniforma riassegnando
    per indice di traccia il colore della colorway — è ciò che `colorway` dovrebbe
    fare da sé se px non avesse già deciso.

    Si toccano solo le tracce a tinta unita: heatmap e scale continue usano una
    colorscale, non un marker, e restano intatte. Se una traccia colora per-punto
    (`marker.color` è un array, non una stringa) non si sovrascrive: quel colore
    porta informazione (es. mappatura su una categoria o su un valore continuo).
    """
    idx = 0
    for tr in fig.data:
        if getattr(tr, "type", None) not in _TINTA_UNITA:
            continue
        colore = colorway[idx % len(colorway)]
        marker = getattr(tr, "marker", None)
        if marker is not None and (marker.color is None or isinstance(marker.color, str)):
            marker.color = colore
        linea = getattr(tr, "line", None)
        if (tr.type in ("scatter", "scattergl") and linea is not None
                and (linea.color is None or isinstance(linea.color, str))):
            linea.color = colore
        idx += 1


def apply_theme(fig):
    """
    Applica il tema visuale coerente (colori, griglia, tipografia) a una figura Plotly.

    La applicano già le fabbriche di questo modulo (`to_chart`, `histogram`,
    `corr_heatmap`) e la sandbox sulle figure prodotte dal codice generato: chi
    disegna riceve figure GIÀ tematizzate e non deve riapplicarla. È idempotente
    (verificato: la seconda applicazione lascia il JSON della figura invariato),
    ma non è gratis — Streamlit ridisegna l'intera pagina a ogni click, quindi
    riapplicarla al momento del render la fa ripagare a ogni interazione.
    Serve solo su una figura costruita con `px` grezzo.
    """
    t = _THEME
    _make_bars_readable(fig)
    _recolor_traces(fig, t["colorway"])
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
        return apply_theme(px.line(data, x=x, y=y, markers=True,
                                   color_discrete_sequence=[_BAR]))

    # Barre: ordina e limita; se le etichette sono lunghe o numerose usa l'orizzontale
    # (nomi leggibili sull'asse y) invece di etichette ruotate e illeggibili.
    try:
        data = data.sort_values(y, ascending=False)
    except Exception as e:  # noqa: BLE001 — colonna non ordinabile: si mostra comunque
        # Loggato e non ignorato: un grafico non ordinato è un difetto visibile
        # all'utente e senza traccia sarebbe impossibile capirne il motivo.
        log.warning("Ordinamento del grafico non riuscito su '%s': %s", y, e)
    labels = data[x].astype(str)
    long_labels = bool(len(labels)) and labels.str.len().max() > 16
    if long_labels or len(data) > 12:
        data = data.head(15).sort_values(y, ascending=True)  # orizzontale: max in alto
        fig = px.bar(data, x=y, y=x, orientation="h", color_discrete_sequence=[_BAR])
    else:
        fig = px.bar(data, x=x, y=y, color_discrete_sequence=[_BAR])
    return apply_theme(fig)


def try_chart(res, kind: str = "bar"):
    """
    Come `to_chart`, ma restituisce None invece di sollevare quando il risultato
    non è graficabile.

    Serve all'avvolgimento automatico: quando l'utente scrive "mostrami il totale
    delle vendite" la risposta è uno scalare, e un numero non si disegna. Con
    `to_chart` quella domanda diventerebbe un errore di esecuzione — e per giunta
    ritentato — invece di una risposta corretta senza grafico. Chi chiama
    `to_chart` di proposito continua a ricevere l'eccezione con la sua diagnosi.
    """
    try:
        return to_chart(res, kind=kind)
    except Exception as e:  # noqa: BLE001 — qualunque motivo: si mostrano i dati
        log.info("Grafico non applicabile al risultato (%s): si mostrano solo i dati.", e)
        return None


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
    values = pd.to_numeric(df[col], errors="coerce").dropna()
    if values.empty:
        raise ValueError(f"La colonna '{col}' non contiene valori numerici.")

    median = float(values.median())
    p_high = float(values.quantile(0.99))
    # Log solo se ha senso: valori strettamente positivi (log10 richiede > 0) e
    # coda destra marcata (il 99° pct dista molte volte dalla mediana). L'euristica
    # è conservativa: dataset "normali" (età, punteggi…) restano in lineare.
    use_log = float(values.min()) > 0 and median > 0 and (p_high / median) > 8

    if use_log:
        import numpy as np
        log_vals = np.log10(values)
        fig = px.histogram(x=log_vals, nbins=nbins, color_discrete_sequence=[_BAR])
        # Etichette dell'asse alle potenze di 10, ma scritte in scala originale
        # (1, 10, 100, 1.000…): l'utente legge i valori veri, non gli esponenti.
        lo, hi = int(np.floor(log_vals.min())), int(np.ceil(log_vals.max()))
        tick = list(range(lo, hi + 1))
        fig.update_xaxes(tickvals=tick, ticktext=[fmt_num(10 ** t) for t in tick])
        fig.update_layout(bargap=0.05, yaxis_title="record", showlegend=False,
                          xaxis_title=f"{col} (scala logaritmica)")
        return apply_theme(fig)

    # Ripiego lineare: limita la vista al 99° percentile se c'è una coda di outlier.
    n_out = int((values > p_high).sum())
    skewed = n_out > 0 and p_high > float(values.min())
    shown = values[values <= p_high] if skewed else values
    fig = px.histogram(shown, nbins=nbins, color_discrete_sequence=[_BAR])
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
