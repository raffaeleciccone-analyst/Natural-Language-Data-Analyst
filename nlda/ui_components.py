"""
Componenti di presentazione (Streamlit) del report e delle risposte.

Estratti da main.py per tenere l'entry-point leggibile: qui sta il "come si
mostra" (card KPI, riquadri, tabelle, grafici collegati), mentre main.py resta
il flusso ("cosa si mostra e quando"). Tutte le funzioni HTML fanno escaping:
i valori delle celle sono dati non fidati e non devono iniettare markup.
"""
import html

import pandas as pd
import streamlit as st

from nlda.executor import apply_theme, to_chart
from nlda.loader import monthly_trend
from nlda.results import ExecutionFailure, ExecutionResult
from nlda.utils import IT_NUM_FORMAT, fmt_num


def answer_card(label: str, text: str) -> None:
    """Renderizza un testo in un riquadro dedicato (con escaping HTML)."""
    safe = html.escape(text).replace("\n", "<br>")
    st.markdown(
        f"<div class='answer-card'>"
        f"<div class='answer-label'>{html.escape(label)}</div>"
        f"<div class='answer-body'>{safe}</div></div>",
        unsafe_allow_html=True,
    )


def readout(col, label: str, value: str, sub: str = "",
            tick: str = "#0e7c86", small: bool = False) -> None:
    """Card KPI in stile 'console': valore monospazio + tacca colorata."""
    # Il sotto-valore è SEMPRE presente (vuoto = spazio riservato) così le card
    # hanno tutte la stessa altezza.
    sub_html = f"<div class='r-sub'>{html.escape(sub) if sub else '&nbsp;'}</div>"
    cls = "r-v sm" if small else "r-v"
    col.markdown(
        f"<div class='readout'>"
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
    return (label, str(ranking.index[0]), fmt_sub(ranking.iloc[0]), "#008300", True)


def build_kpis(df, sel_measure, sel_category, unit):
    """
    Costruisce le card KPI adattandosi ai dati: con una MISURA -> totale/media/
    massimo/leader; SENZA misura -> conteggi. Ritorna una lista di tuple
    (label, value, sub, tick, small). Nessun effetto Streamlit (facile da testare).
    Su dataset vuoto non solleva: ripiega sulle card a conteggio.
    """
    def wu(v):  # accosta l'unità di misura, se indicata
        return f"{fmt_num(v)} {unit}".strip() if unit else fmt_num(v)

    record_kpi = ("Record", fmt_num(len(df)), "", "#008300", False)

    kpis = []
    if sel_measure:
        s = df[sel_measure]
        kpis.append((f"Totale {sel_measure}", wu(s.sum()), "", "#0e7c86", False))
        kpis.append((f"Media {sel_measure}", wu(s.mean()), "", "#eda100", False))
        kpis.append((f"Massimo {sel_measure}", wu(s.max()), "", "#2a78d6", False))
        leader = None
        if sel_category:
            ranking = df.groupby(sel_category)[sel_measure].sum().sort_values(ascending=False)
            leader = _leader_kpi(f"Top {sel_category}", ranking, wu)
        # Senza categoria (o senza un leader calcolabile) la quarta card resta il conteggio
        kpis.append(leader or record_kpi)
    elif sel_category:  # nessuna misura: KPI a conteggi
        vc = df[sel_category].value_counts()
        kpis.append(("Record", fmt_num(len(df)), "", "#2a78d6", False))
        kpis.append((f"{sel_category} distinte", fmt_num(df[sel_category].nunique()), "", "#eda100", False))
        leader = _leader_kpi(f"Top {sel_category}", vc, lambda v: f"{fmt_num(v)} record")
        if leader is not None:
            kpis.append(leader)
    else:
        kpis.append(("Record", fmt_num(len(df)), "", "#2a78d6", False))
        kpis.append(("Colonne", str(df.shape[1]), "", "#008300", False))
    return kpis


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
    graf_cols = st.columns(2 if both else 1)
    idx = 0
    selected_cat = None

    if top_fig is not None:
        cat, num, _ = insights["top"]
        with graf_cols[idx]:
            st.markdown(f"**Classifica: {num} per {cat}**")
            event = st.plotly_chart(apply_theme(top_fig), width="stretch",
                                    on_select="rerun", key="report_top")
            try:
                pts = event.selection.points  # type: ignore[attr-defined]
                if pts:
                    px_ = pts[0].get("x")
                    selected_cat = px_ if isinstance(px_, str) else pts[0].get("y")
            except Exception:
                selected_cat = None
        idx += 1

    if trend_fig is not None and "trend" in insights:
        dcol, num, _ = insights["trend"]
        cat = insights["top"].key if "top" in insights else None
        with graf_cols[idx]:
            sub = None
            if selected_cat is not None and cat is not None:
                key_val = str(selected_cat).rstrip("…")
                mask = df[cat].astype(str) == str(selected_cat)
                if not mask.any():
                    mask = df[cat].astype(str).str.startswith(key_val)
                sub = monthly_trend(df[mask], dcol, insights.get("measure"))

            if sub is not None:
                st.markdown(f"**Andamento di {num} — {cat}: {selected_cat}**")
                st.plotly_chart(apply_theme(to_chart(sub, kind="line")),
                                width="stretch", key="report_trend_filtered")
            else:
                st.markdown(f"**Andamento di {num} nel tempo**")
                st.plotly_chart(apply_theme(trend_fig), width="stretch", key="report_trend")


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
    elif isinstance(value, str) and value != "Codice eseguito correttamente.":
        st.markdown(f"**Risultato:** {value}")


def render_result(code: str, result: ExecutionResult,
                  explanation: "str | None" = None, kp: str = "r") -> None:
    """Rende un turno completo: risposta testuale, risultato visuale, codice generato."""
    # 1. Risposta testuale (in un riquadro dedicato)
    if explanation:
        answer_card("Risposta", explanation)

    # 2. Risultato visuale: grafico E/O dati. All'utente si mostra solo il
    # messaggio del fallimento: la causa tecnica ('kind') serve al codice, non a lui.
    if isinstance(result, ExecutionFailure):
        st.error(result.message)
    else:
        if result.fig is not None:
            st.plotly_chart(apply_theme(result.fig), width="stretch", key=f"{kp}_fig")
        render_value(result.value, kp)

    # 3. Codice generato (in fondo, collassato)
    with st.expander("Codice Pandas generato"):
        st.code(code, language="python")
