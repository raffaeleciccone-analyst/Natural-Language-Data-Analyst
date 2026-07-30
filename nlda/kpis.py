"""
Le card KPI in cima al report: quali sono e cosa dicono.

## Perché sta qui e non in `ui_components.py`

Ci è nata, insieme ai componenti Streamlit. Ma è pandas puro — nessuna chiamata
`st.`, come dichiarava già il suo docstring — e restare in un modulo che fa
`import streamlit` significava che l'API HTTP, per calcolare quattro numeri,
importava l'intera interfaccia grafica di un'altra interfaccia.

Il costo non era teorico: **864 ms e 38 MB** di Streamlit a ogni avvio di un
servizio che non disegna mai una pagina Streamlit — pagati anche a ogni avvio a
freddo del container. È la stessa mossa già fatta per il filtro e l'unione in
`nlda/views.py`, applicata dove era rimasta indietro.

## Il formato di ritorno

Una lista di tuple `(etichetta, valore, sotto, tacca, piccolo)`. Il VALORE è già
formattato all'italiana e con l'unità accostata: la convenzione vive in
`nlda.utils.fmt_num` ed è la stessa per entrambe le interfacce. Restituire un
numero grezzo significherebbe farlo formattare due volte, in due linguaggi, con
due arrotondamenti — ed è esattamente il modo in cui la stessa cifra finisce per
leggersi diversa nelle due pagine.
"""
import pandas as pd

from nlda.ui_theme import PALETTE
from nlda.utils import fmt_num

# La tacca colorata sotto il valore. `PALETTE` è la fonte unica dei colori del
# progetto e non importa nulla, quindi si può leggere anche da qui senza
# trascinarsi dietro Streamlit.
_TICK = PALETTE["accent"]   # accento standard (Media, Massimo, Top, conteggi)
_TICK_MAIN = "#15b8a1"      # più brillante, riservato al Totale: esiste solo qui


def _leader_kpi(label: str, ranking: pd.Series, fmt_sub):
    """
    Card 'Top ...' costruita dalla prima riga di una classifica, oppure None se la
    classifica è VUOTA. Il caso vuoto è reale e raggiungibile dalla UI: un CSV con le
    sole intestazioni, oppure una colonna categoriale con soli valori mancanti (il
    groupby scarta i NaN). Senza questa guardia `ranking.index[0]` solleva IndexError
    e l'interfaccia mostra la schermata d'errore al posto dell'app.
    """
    if ranking.empty:
        return None
    return (label, str(ranking.index[0]), fmt_sub(ranking.iloc[0]), _TICK, True)


def build_kpis(df, sel_measure, sel_category, unit):
    """
    Costruisce le card KPI adattandosi ai dati: con una MISURA -> totale/media/
    massimo/leader; SENZA misura -> conteggi. Ritorna una lista di tuple
    (label, value, sub, tick, small). Funzione pura: nessun effetto di
    presentazione, quindi facile da testare.
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
        kpis.append((f"{sel_category} distinte",
                     fmt_num(df[sel_category].nunique()), "", _TICK, False))
        leader = _leader_kpi(f"Top {sel_category}", vc, lambda v: f"{fmt_num(v)} record")
        if leader is not None:
            kpis.append(leader)
    else:
        kpis.append(record_kpi)
        kpis.append(("Colonne", str(df.shape[1]), "", _TICK, False))
    return kpis
