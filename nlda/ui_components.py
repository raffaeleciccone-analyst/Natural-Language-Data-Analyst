"""
Componenti di presentazione (Streamlit) del report e delle risposte.

Qui stanno i pezzi riusabili — il "come si mostra": card KPI, riquadri, tabelle,
grafici collegati. Le sezioni che li compongono sono in `nlda.ui.pages`, il flusso
("cosa si mostra e quando") in `main.py`. Tutte le funzioni HTML fanno escaping:
i valori delle celle sono dati non fidati e non devono iniettare markup.
"""
import html

import pandas as pd
import streamlit as st

from nlda.charts import to_chart
from nlda.checks import claimed_missing_columns, columns_referenced, sanity_warnings
from nlda.loader import monthly_trend
from nlda.results import EXECUTED_OK, ExecutionFailure, ExecutionResult
from nlda.ui_theme import PALETTE
from nlda.utils import IT_NUM_FORMAT, fmt_num

# Tacca colorata sotto i KPI: teal per tutte (coerenza), ma il KPI PRINCIPALE
# (Totale) usa un teal più LUMINOSO, così risalta sugli altri.
_TICK = PALETTE["accent"]   # accento standard (Media, Massimo, Top, conteggi)
_TICK_MAIN = "#15b8a1"      # più brillante, riservato al Totale: esiste solo qui


def answer_card(label: str, text: str, container=None) -> None:
    """
    Renderizza un testo in un riquadro dedicato (con escaping HTML).

    `container` permette di scrivere dentro uno spazio riservato in precedenza con
    `st.empty()`: serve a disegnare la pagina subito e riempire più tardi la parte
    che deve attendere il modello.
    """
    # NB: qui il testo va in HTML (unsafe_allow_html), NON in markdown: si fa solo
    # l'escape HTML. Niente escape dei `$` — questo riquadro non li interpreta come
    # LaTeX, e un `\$` ci comparirebbe col backslash in chiaro.
    safe = html.escape(text).replace("\n", "<br>")
    (container or st).markdown(
        f"<div class='answer-card'>"
        f"<div class='answer-label'>{html.escape(label)}</div>"
        f"<div class='answer-body'>{safe}</div></div>",
        unsafe_allow_html=True,
    )


def readout(col, label: str, value: str, sub: str = "",
            tick: str = "#0d8a7d", small: bool = False) -> None:
    """Card KPI in stile 'console': valore monospazio + tacca colorata."""
    # Il sotto-valore è SEMPRE presente (vuoto = spazio riservato) così le card
    # hanno tutte la stessa altezza.
    sub_html = f"<div class='r-sub'>{html.escape(sub) if sub else '&nbsp;'}</div>"
    cls = "r-v sm" if small else "r-v"
    # Quanto può essere grande il valore dipende da DUE cose che il CSS da solo non
    # sa mettere insieme: la larghezza della card (cambia col responsive) e la
    # LUNGHEZZA della cifra, che dipende dai dati — "9.412" e "1.284.377.905,40 €"
    # non occupano lo stesso spazio. La seconda si conosce solo qui, ed è per quello
    # che il calcolo sta in Python: al CSS si passa un coefficiente espresso in `cqi`
    # (1% della larghezza della card) e il browser lo riapplica a ogni ridimensiona-
    # mento, senza una riga di JavaScript.
    # 153 ≈ 100 / (0.6 × 0.92): 0.6em è la larghezza di un carattere del monospazio,
    # 0.92 il margine che tiene il valore staccato dai bordi. Il `min()` nel CSS
    # impedisce che su una card larga il valore superi i 1.9rem di progetto: questo
    # coefficiente può solo RIMPICCIOLIRE, mai ingrandire oltre il tema.
    vfs = round(153 / max(len(value), 1), 1)
    col.markdown(
        f"<div class='readout' style='--vfs:{vfs}'>"
        f"<div class='r-k'>{html.escape(label)}</div>"
        f"<div class='{cls}'>{html.escape(value)}</div>"
        f"<div class='r-tick' style='--bar:{tick}'></div>{sub_html}</div>",
        unsafe_allow_html=True,
    )


def _leader_kpi(label: str, ranking: pd.Series, fmt_sub):
    """
    Card 'Top ...' costruita dalla prima riga di una classifica, oppure None se la
    classifica è VUOTA. Il caso vuoto è reale e raggiungibile dalla UI: un CSV con le
    sole intestazioni, oppure una colonna categoriale con soli valori mancanti (il
    groupby scarta i NaN). Senza questa guardia `ranking.index[0]` solleva IndexError
    e Streamlit mostra la schermata d'errore al posto dell'app.
    """
    if ranking.empty:
        return None
    return (label, str(ranking.index[0]), fmt_sub(ranking.iloc[0]), _TICK, True)


def build_kpis(df, sel_measure, sel_category, unit):
    """
    Costruisce le card KPI adattandosi ai dati: con una MISURA -> totale/media/
    massimo/leader; SENZA misura -> conteggi. Ritorna una lista di tuple
    (label, value, sub, tick, small). Nessun effetto Streamlit (facile da testare).
    Su dataset vuoto non solleva: ripiega sulle card a conteggio.
    """
    def wu(v):  # accosta l'unità di misura, se indicata
        return f"{fmt_num(v)} {unit}".strip() if unit else fmt_num(v)

    record_kpi = ("Record", fmt_num(len(df)), "", _TICK, False)

    kpis = []
    if sel_measure:
        s = df[sel_measure]
        kpis.append((f"Totale {sel_measure}", wu(s.sum()), "", _TICK_MAIN, False))
        kpis.append((f"Media {sel_measure}", wu(s.mean()), "", _TICK, False))
        kpis.append((f"Massimo {sel_measure}", wu(s.max()), "", _TICK, False))
        leader = None
        if sel_category:
            ranking = df.groupby(sel_category)[sel_measure].sum().sort_values(ascending=False)
            leader = _leader_kpi(f"Top {sel_category}", ranking, wu)
        # Senza categoria (o senza un leader calcolabile) la quarta card resta il conteggio
        kpis.append(leader or record_kpi)
    elif sel_category:  # nessuna misura: KPI a conteggi
        vc = df[sel_category].value_counts()
        kpis.append(record_kpi)
        kpis.append((f"{sel_category} distinte", fmt_num(df[sel_category].nunique()), "", _TICK, False))
        leader = _leader_kpi(f"Top {sel_category}", vc, lambda v: f"{fmt_num(v)} record")
        if leader is not None:
            kpis.append(leader)
    else:
        kpis.append(record_kpi)
        kpis.append(("Colonne", str(df.shape[1]), "", _TICK, False))
    return kpis


def _clicked_category(point: dict):
    """
    Valore ESATTO della barra cliccata. Le etichette lunghe vengono troncate con '…'
    sull'asse (vedi charts._make_bars_readable), ma il valore intero resta in
    `customdata`: leggerlo da lì evita di ricostruirlo da un'etichetta accorciata.
    Fallback su x/y quando customdata non c'è (etichette corte, non troncate): lì il
    valore sull'asse è già quello completo. Per le barre orizzontali la categoria è
    su y, per quelle verticali su x — si prende la prima che sia una stringa.
    """
    cd = point.get("customdata")
    if cd:
        return cd[0] if isinstance(cd, (list, tuple)) else cd
    x = point.get("x")
    return x if isinstance(x, str) else point.get("y")


def render_linked_charts(df, insights, top_fig, trend_fig) -> None:
    """
    Classifica + andamento affiancati e COLLEGATI: cliccando una barra della
    classifica l'andamento si filtra sulla categoria scelta (ri-cliccando si
    toglie il filtro). Isola qui la logica di interazione del report.
    """
    if top_fig is None and trend_fig is None:
        return
    both = top_fig is not None and trend_fig is not None
    if both:
        st.caption("Clicca una barra della classifica per filtrare l'andamento; "
                   "ri-clicca la stessa barra per togliere il filtro.")
    chart_cols = st.columns(2 if both else 1)
    idx = 0
    selected_cat = None

    if top_fig is not None:
        cat, num, _ = insights["top"]
        with chart_cols[idx]:
            st.markdown(f"**Classifica: {num} per {cat}**")
            event = st.plotly_chart(top_fig, width="stretch",
                                    on_select="rerun", key="report_top")
            try:
                pts = event.selection.points  # type: ignore[attr-defined]
                if pts:
                    selected_cat = _clicked_category(pts[0])
            except Exception:
                selected_cat = None
        idx += 1

    if trend_fig is not None and "trend" in insights:
        dcol, num, _ = insights["trend"]
        cat = insights["top"].key if "top" in insights else None
        with chart_cols[idx]:
            sub = None
            if selected_cat is not None and cat is not None:
                # `selected_cat` è il valore ESATTO (da customdata quando l'etichetta
                # era troncata), quindi il confronto è diretto: niente più rstrip('…')
                # + startswith per indovinare da un'etichetta accorciata.
                mask = df[cat].astype(str) == str(selected_cat)
                sub = monthly_trend(df[mask], dcol, insights.get("measure"))

            if sub is not None:
                st.markdown(f"**Andamento di {num} — {cat}: {selected_cat}**")
                st.plotly_chart(to_chart(sub, kind="line"),
                                width="stretch", key="report_trend_filtered")
            else:
                st.markdown(f"**Andamento di {num} nel tempo**")
                st.plotly_chart(trend_fig, width="stretch", key="report_trend")


def render_value(value, kp: str = "r") -> None:
    """Mostra il dato del risultato (tabella / numero / testo), con numeri leggibili."""
    if value is None:
        return
    if isinstance(value, pd.DataFrame):
        num_cols = list(value.select_dtypes("number").columns)
        styled = (value.style.format(subset=num_cols, **IT_NUM_FORMAT)
                  if num_cols else value)
        st.dataframe(styled, width="stretch", hide_index=True, key=f"{kp}_df")
    elif isinstance(value, pd.Series):
        st.dataframe(value.rename("valore").to_frame(), width="stretch", key=f"{kp}_ser")
    elif isinstance(value, (int, float)):
        st.metric("Risultato", fmt_num(value))
    elif isinstance(value, str) and value != EXECUTED_OK:
        st.markdown(f"**Risultato:** {value}")


def render_result(code: str, result: ExecutionResult,
                  explanation: "str | None" = None, kp: str = "r", columns=None,
                  question: str = "") -> None:
    """
    Rende un turno completo: risposta testuale, eventuali avvisi, risultato visuale
    e codice generato con le colonne usate.

    `columns` (le colonne del dataset) abilita la riga "Colonne usate": mostra su
    cosa poggia la risposta, così l'utente può fidarsi o verificare. `question`
    abilita l'avviso quando la domanda nomina una colonna che non esiste.
    """
    # 0. Avviso anti-allucinazione, PRIMA della risposta: se la domanda nomina
    # esplicitamente una colonna inesistente, il modello tende a sostituirla in
    # silenzio con una reale e a spacciarla per quella chiesta. Lo si dice qui, in
    # chiaro, non lo si nasconde nel pannello del codice.
    if columns is not None and question:
        inventate = claimed_missing_columns(question, columns)
        if inventate:
            etichetta = ", ".join(f"«{c}»" for c in inventate)
            verbo = "non è una colonna" if len(inventate) == 1 else "non sono colonne"
            base = columns_referenced(code, columns)
            suffisso = f" La risposta si basa su: {', '.join(base)}." if base else ""
            st.warning(f"⚠️ {etichetta} {verbo} del dataset.{suffisso}")

    # 1. Risposta testuale (in un riquadro dedicato)
    if explanation:
        answer_card("Risposta", explanation)

    # 2. Risultato visuale: grafico E/O dati. All'utente si mostra solo il
    # messaggio del fallimento: la causa tecnica ('kind') serve al codice, non a lui.
    if isinstance(result, ExecutionFailure):
        st.error(result.message)
    else:
        # Sanity check PRIMA dei dati: se il risultato è sospetto, l'avviso si vede
        # accanto al numero, non nascosto in fondo.
        for avviso in sanity_warnings(result.value):
            st.warning(f"⚠️ {avviso}")
        if result.fig is not None:
            st.plotly_chart(result.fig, width="stretch", key=f"{kp}_fig")
        render_value(result.value, kp)

    # 3. Codice generato (in fondo, collassato) + le colonne del dataset che tocca:
    # la trasparenza che trasforma "fidati" in "verifica".
    with st.expander("Codice Pandas generato"):
        st.code(code, language="python")
        if columns is not None:
            usate = columns_referenced(code, columns)
            if usate:
                st.caption("Colonne usate: " + ", ".join(usate))
