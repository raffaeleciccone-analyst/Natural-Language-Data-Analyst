"""
Punto di ingresso Streamlit dell'applicazione.

Qui resta solo il FLUSSO della pagina: cosa si mostra e in quale ordine (`main()`).
Il layout delle singole sezioni sta in `nlda.ui.pages`, il plumbing (secret, quota,
cache, caricamento dati) in `nlda.ui.session`, i numeri in `nlda.loader`, il turno
di conversazione in `nlda.service`.

Importare questo modulo non disegna nulla: `st.set_page_config` è dentro
`configure_page()`, chiamata da `main()` sotto la guardia `__main__`. Così i pezzi
dell'app restano raggiungibili da un test (vedi `tests/test_main_history.py`).
"""
import html
from pathlib import Path

import streamlit as st

from nlda.loader import dataset_signature
from nlda.sandbox.pool import riserva
from nlda.service import AnalysisService
from nlda.ui.pages import (
    fill_overview,
    refresh_report_state,
    render_chat,
    render_executive_report,
    render_filter,
    render_join,
    render_kpis,
    render_period_comparison,
    render_report,
    render_report_selectors,
    render_sidebar_config,
)
from nlda.ui.session import apply_filter, demo_limits, get_agent, load_dataframe
from nlda.ui_theme import console_css
from nlda.utils import fmt_num

# Favicon: logo del progetto (PNG trasparente in assets/); ripiego sull'emoji se
# il file non è presente, così l'app parte comunque. Resta qui — non in nlda/ui/ —
# perché il path è relativo alla RADICE del repo, dove vive assets/.
_FAVICON = Path(__file__).parent / "assets" / "favicon.png"


def configure_page() -> None:
    """Deve essere la PRIMA chiamata Streamlit dello script."""
    st.set_page_config(
        page_title="Natural Language Data Analyst",
        page_icon=str(_FAVICON) if _FAVICON.exists() else "📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def main() -> None:
    """Flusso della pagina, dall'alto verso il basso."""
    configure_page()
    limits = demo_limits()
    config = render_sidebar_config(limits)

    # Lo stile va iniettato dopo la sidebar (che lo usa) e prima del contenuto.
    st.markdown(console_css(), unsafe_allow_html=True)

    agent = get_agent(config.provider, config.model_name, config.api_key)
    service = AnalysisService(agent)

    # Prepara un worker mentre l'utente legge il report: l'avvio costa ~840 ms di
    # import (pandas, plotly) e pagarli qui significa non pagarli alla prima
    # domanda. È idempotente, quindi i rerun continui di Streamlit non lo ripetono.
    riserva.prewarm()

    st.title("Natural Language Data Analyst")
    st.markdown(
        f"<p class='app-subtitle'>Interroga i tuoi dati in linguaggio naturale — "
        f"provider attivo: <b>{html.escape(config.provider)}</b> · "
        f"<code>{html.escape(config.model_name)}</code></p>",
        unsafe_allow_html=True,
    )

    df_full, source_label = load_dataframe(config.uploaded_file)
    if df_full is None:
        st.info("Carica un file CSV dalla barra laterale per iniziare.")
        st.stop()

    # Join opzionale con un secondo file: se presente, da qui in poi df_full è il df
    # UNITO. È un preprocessing — il resto della pipeline resta a un solo DataFrame.
    df_full, source_label = render_join(df_full, source_label)

    sel_measure, sel_category, unit = render_report_selectors(df_full)
    # Il filtro restringe l'INTERA pagina (report, KPI, confronto, chat) e resta
    # attivo tra i turni. La firma del dataset si calcola sul df INTERO: cambiare
    # filtro non azzera la conversazione, cambiare file sì.
    filtro = render_filter(df_full)
    df, filtro_label = apply_filter(df_full, filtro)
    data_sig = dataset_signature(df_full, source_label)

    if filtro_label:
        st.caption(f"{source_label} — 🔎 filtro attivo: {filtro_label} · "
                   f"{fmt_num(len(df))} righe su {fmt_num(len(df_full))}")
    else:
        st.caption(f"{source_label} — {fmt_num(len(df))} righe · {df.shape[1]} colonne")

    render_kpis(df, sel_measure, sel_category, unit)

    with st.expander("Anteprima dei dati (prime 10 righe)"):
        st.dataframe(df.head(10), width="stretch")

    report_sig, insights = refresh_report_state(df, data_sig, sel_measure, sel_category,
                                                filter_key=filtro or ())

    slot_sintesi = render_report(df, insights, sel_measure, unit)
    render_period_comparison(df, sel_measure, unit)
    render_executive_report(
        agent, insights, limits,
        exec_sig=(report_sig, config.provider, config.model_name, unit), unit=unit,
    )
    render_chat(service, df, limits, explain=config.explain, unit=unit,
                dataset_label=source_label)

    # PER ULTIMA, anche se compare in cima alla pagina: è l'unica parte che
    # aspetta il modello. Generandola qui, l'utente ha già davanti KPI, tabelle,
    # grafici e il box domanda mentre la sintesi arriva. La firma include
    # provider e unità perché anche quelli cambiano il testo prodotto.
    fill_overview(
        slot_sintesi, agent, insights, unit,
        overview_sig=(report_sig, config.explain, config.provider, config.model_name, unit),
        explain=config.explain,
    )


if __name__ == "__main__":
    main()
