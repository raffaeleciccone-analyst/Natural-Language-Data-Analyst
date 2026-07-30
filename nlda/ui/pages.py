"""
Sezioni della pagina Streamlit: le funzioni `render_*` che disegnano la barra
laterale, i KPI, il report e la chat. Solo LAYOUT — i numeri li calcola
`nlda.loader`, il turno lo orchestra `nlda.service`, il plumbing (secret, quota,
cache, caricamento) sta in `nlda.ui.session`. `main.py` chiama queste funzioni
nell'ordine giusto.
"""
from dataclasses import replace

import pandas as pd
import streamlit as st

from nlda.agent import DataAgent
from nlda.charts import apply_theme, corr_heatmap, histogram, px, to_chart, try_fig
from nlda.demo import DemoLimits
from nlda.export import conversation_to_markdown
from nlda.kpis import build_kpis
from nlda.loader import (
    SUPPORTED_EXTENSIONS,
    analyze,
    best_category,
    category_columns,
    default_unit,
    measure_columns,
    ordered_measures,
    profile,
)
from nlda.periods import compare_periods
from nlda.project_qa import answer as answer_about_project
from nlda.providers import DEFAULT_MODELS, REQUIRES_API_KEY, available_providers
from nlda.results import ExecutionFailure, ExecutionSuccess
from nlda.service import AnalysisService, Turn
from nlda.ui.session import (
    _KEY_ENV,
    SidebarConfig,
    _secret,
    demo_allows,
    demo_consume,
    load_second_dataset,
)
from nlda.ui_components import (
    answer_card,
    readout,
    render_linked_charts,
    render_result,
)
from nlda.utils import fmt_num, md_safe, with_unit
from nlda.views import join_datasets


def render_sidebar_config(limits: DemoLimits) -> SidebarConfig:
    """Prima parte della barra laterale: modello e sorgente dati."""
    with st.sidebar:
        st.header("Configurazione")
        explain = st.toggle("Spiegazione AI", value=True,
                            help="Genera una risposta testuale che interpreta il risultato.")
        st.divider()
        st.subheader("Modello LLM")

        if limits.enabled:
            provider = _secret("PROVIDER", "groq").strip().lower()
            model_name = _secret("MODEL", DEFAULT_MODELS.get(provider, ""))
            api_key = _secret(_KEY_ENV.get(provider, ""), "")
            st.success(f"Demo pubblica · **{provider}** · `{model_name}`")
            st.caption(f"Limite: {limits.max_questions} domande per sessione. "
                       "Clona il repo per uso illimitato e per usare Ollama in locale.")
        else:
            provider = st.selectbox(
                "Provider", available_providers(),
                help="Ollama gira in locale; Anthropic, OpenAI e Gemini richiedono una API key.",
            )
            model_name = st.text_input("Modello", value=DEFAULT_MODELS[provider])
            api_key = ""
            if provider in REQUIRES_API_KEY:
                api_key = st.text_input(
                    "API Key", type="password",
                    help="Lascia vuoto per usare la variabile d'ambiente "
                         "(ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY).",
                )
                if not api_key:  # ripiego su secret/env
                    api_key = _secret(_KEY_ENV.get(provider, ""), "")

        st.divider()
        st.subheader("Dataset")
        uploaded_file = st.file_uploader(
            "Carica un file", type=SUPPORTED_EXTENSIONS,
            help="Formati supportati: CSV, Excel (.xlsx/.xls), JSON.",
        )

    return SidebarConfig(provider=provider, model_name=model_name, api_key=api_key,
                         explain=explain, uploaded_file=uploaded_file)


def render_join(left: pd.DataFrame, left_label: str):
    """
    Join OPZIONALE con un secondo dataset, nella barra laterale. Ritorna
    `(df, etichetta)`: il DataFrame unito, oppure l'originale se non c'è un secondo
    file. Il join produce UN solo df, che alimenta la pipeline come sempre — sandbox,
    prompt e report non sanno nemmeno che c'erano due file.
    """
    # Funzione avanzata: sta in un expander CHIUSO per non allungare la sidebar (e per
    # non spingere il filtro così in basso che il suo menu finisce sotto lo schermo).
    with st.sidebar, st.expander("Unisci un secondo dataset"):
        second = st.file_uploader("Secondo file (opzionale, per un join)",
                                  type=SUPPORTED_EXTENSIONS, key="join_file")
        if second is None:
            return left, left_label
        try:
            right = load_second_dataset(second)
        except Exception as e:  # noqa: BLE001 — file dell'utente: si mostra, non si esplode
            st.error(f"Secondo file illeggibile: {e}")
            return left, left_label

        c1, c2 = st.columns(2)
        left_on = c1.selectbox("Chiave nel primo file", list(left.columns), key="join_lk")
        right_on = c2.selectbox("Chiave nel secondo", list(right.columns), key="join_rk")
        how = st.selectbox(
            "Tipo di join", ["inner", "left"], key="join_how",
            help="inner: solo le righe con corrispondenza in entrambi; "
                 "left: tutte le righe del primo file, con i campi del secondo dove combaciano.",
        )
        try:
            merged = join_datasets(left, right, left_on, right_on, how=how)
        except Exception as e:  # noqa: BLE001 — chiavi incompatibili: si spiega
            st.error(f"Join non riuscito: {e}")
            return left, left_label

        st.caption(f"Unione riuscita: {len(merged)} righe · {merged.shape[1]} colonne.")
        return merged, f"{left_label} ⨝ {second.name}"


def render_report_selectors(df: pd.DataFrame) -> tuple[str | None, str | None, str]:
    """Seconda parte della barra laterale: misura, categoria e unità di misura.

    Misura e categoria sono `None` quando il dataset non ne offre: il tipo di
    ritorno lo dichiara, così chi le riceve non le tratta per stringhe sicure."""
    measures = ordered_measures(measure_columns(df))
    cats = category_columns(df)
    with st.sidebar:
        st.divider()
        st.subheader("Report")
        if measures:
            sel_measure = st.selectbox("Misura", measures, index=0,
                                       help="La colonna numerica su cui basare KPI e classifiche.")
        else:
            sel_measure = None
            st.caption("Nessuna colonna numerica: report a conteggi.")

        if cats:
            preferred = best_category(df)
            idx = cats.index(preferred) if preferred in cats else 0
            sel_category = st.selectbox("Categoria", cats, index=idx,
                                        help="La dimensione per classifiche e filtri.")
        else:
            sel_category = None

        unit = st.text_input("Unità di misura (opzionale)", value="",
                             placeholder="es. €, kg, %, unità",
                             help="Mostrata accanto ai valori nei KPI e nelle risposte. "
                                  "Per misure economiche senza unità si usa $ come "
                                  "standard.").strip()
    return sel_measure, sel_category, unit or default_unit(sel_measure)


def render_filter(df: pd.DataFrame):
    """
    Filtro PERSISTENTE per una colonna categoriale, nella barra laterale. Ritorna lo
    spec `(colonna, valori)` — hashable, usato anche come chiave di ricalcolo — o None.

    Resta attivo tra un turno e l'altro: il df filtrato che ne esce alimenta l'intera
    pagina (report, KPI, confronto e chat), quindi "guardo solo il Nord" vale per tutto
    finché non lo si toglie. Il service resta stateless: riceve semplicemente il df già
    ristretto, non deve ricordare nulla.
    """
    cats = category_columns(df)
    with st.sidebar:
        st.divider()
        st.subheader("Filtro")
        if not cats:
            st.caption("Nessuna colonna categoriale da filtrare.")
            return None
        col = st.selectbox("Filtra per colonna", ["(nessun filtro)", *cats], key="flt_col")
        if col == "(nessun filtro)":
            return None
        valori = sorted(df[col].dropna().astype(str).unique().tolist())
        scelti = st.multiselect("Tieni solo questi valori", valori, key="flt_vals")
        return (col, tuple(scelti)) if scelti else None


def render_kpis(df: pd.DataFrame, sel_measure, sel_category, unit: str) -> None:
    kpis = build_kpis(df, sel_measure, sel_category, unit)
    for col, (label, value, sub, tick, small) in zip(st.columns(len(kpis)), kpis, strict=False):
        readout(col, label, value, sub=sub, tick=tick, small=small)
    st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)


def refresh_report_state(df: pd.DataFrame, data_sig, sel_measure, sel_category, filter_key=()):
    """
    Ricalcola profilo, insight e figure solo quando serve davvero.

    Le firme sono a granularità diversa di proposito: `data_sig` identifica il FILE
    (il df intero, calcolato a monte), quindi cambiare FILTRO non azzera la
    conversazione — cambiare dataset sì. Il filtro entra invece in `report_sig`, così
    report, profilo e figure si ricalcolano sul df FILTRATO quando il filtro cambia.
    """
    if st.session_state.get("dataset_sig") != data_sig:
        st.session_state.dataset_sig = data_sig
        st.session_state.messages = []

    report_sig = (data_sig, filter_key, sel_measure, sel_category)
    if st.session_state.get("report_sig") != report_sig:
        st.session_state.report_sig = report_sig
        st.session_state.profile = profile(df)
        with st.spinner("Analisi del dataset in corso..."):
            insights = analyze(df, measure=sel_measure, category=sel_category)
            st.session_state.insights = insights
            st.session_state.top_fig = (
                try_fig(to_chart, insights["top"].data, kind="bar") if "top" in insights else None)
            st.session_state.trend_fig = (
                try_fig(to_chart, insights["trend"].data, kind="line")
                if "trend" in insights else None)
            st.session_state.corr_fig = (
                try_fig(corr_heatmap, insights["corr"]) if "corr" in insights else None)
            st.session_state.dist_fig = (
                try_fig(histogram, df, sel_measure) if sel_measure else None)

    return report_sig, st.session_state.get("insights", {})


def render_report(df: pd.DataFrame, insights: dict, sel_measure, unit: str):
    """
    Report iniziale: insight deterministici, tabelle e grafici.

    Ritorna lo SPAZIO RISERVATO alla sintesi AI, che viene riempito più tardi
    (vedi `fill_overview`). I numeri sono già pronti quando questa funzione gira:
    farli aspettare la chiamata al modello significherebbe mostrare una pagina
    vuota per decine di secondi con un modello locale.
    """
    st.subheader("Report iniziale sui dati")

    slot_sintesi = st.empty()
    # Lo spazio si prenota SUBITO. `st.empty()` da solo ha altezza zero: quando la
    # sintesi arriva — secondi dopo, a pagina ormai ferma — spinge in basso tutto
    # quello che l'utente sta leggendo. Con l'altezza riservata il testo compare
    # dentro un riquadro che c'era già.
    slot_sintesi.markdown("<div class='sintesi-slot'></div>", unsafe_allow_html=True)

    # Insight automatici (numeri calcolati in Pandas, non dedotti dall'AI).
    # Via answer_card: il testo è HTML-escaped, così un valore di cella ostile
    # non può iniettare Markdown/HTML nella pagina.
    if insights.get("findings"):
        answer_card("Insight automatici", "\n".join(f"• {f}" for f in insights["findings"]))

    if "numeric_stats" in insights:
        st.markdown("**Statistiche delle colonne numeriche**")
        stats = insights["numeric_stats"].copy()
        for column in ["Somma", "Media", "Minimo", "Massimo"]:
            if column in stats.columns:
                stats[column] = stats[column].map(fmt_num)
        st.dataframe(stats, width="stretch", hide_index=True)

    render_linked_charts(df, insights, st.session_state.get("top_fig"),
                         st.session_state.get("trend_fig"))

    _render_distribution_and_correlations(insights, sel_measure)

    return slot_sintesi


def render_column_structure() -> None:
    """Expander col profilo delle colonne (tipi, mancanti, valori). Estratto da
    render_report per poterlo tenere IN FONDO alla colonna, dopo il report esecutivo
    e il confronto tra periodi (è materiale di dettaglio, non di apertura)."""
    with st.expander("Struttura delle colonne (tipi, mancanti, valori)"):
        st.dataframe(st.session_state.get("profile"), width="stretch", hide_index=True)


def fill_overview(slot, agent: DataAgent, insights: dict, unit: str,
                  overview_sig: tuple, explain: bool) -> None:
    """
    Genera la sintesi AI e la scrive nello spazio riservato dal report.

    Va chiamata per ULTIMA, dopo che tutto il resto della pagina è stato
    disegnato: è l'unica parte che aspetta il modello, e con Ollama in locale
    sono decine di secondi. Prima veniva generata in cima al flusso e bloccava
    l'intera pagina — l'utente vedeva uno schermo vuoto mentre i numeri erano
    già calcolati.

    Su rerun successivi il testo è in cache e compare subito: si attende solo
    quando cambia davvero qualcosa (dati, misura, provider, unità).
    """
    da_generare = st.session_state.get("overview_sig") != overview_sig
    if da_generare:
        st.session_state.overview_sig = overview_sig
        if explain and insights.get("text"):
            # Segnale immediato nello spazio riservato: la pagina è già leggibile,
            # e qui si dice che manca solo questo pezzo.
            slot.markdown(
                "<div class='sintesi-slot'><p class='attesa-sintesi'>"
                "L'AI sta preparando la sintesi dei dati…</p></div>",
                unsafe_allow_html=True)
            st.session_state.overview_text = agent.overview(with_unit(insights["text"], unit))
        else:
            st.session_state.overview_text = None

    if st.session_state.get("overview_text"):
        answer_card("Sintesi dei dati", st.session_state.overview_text, container=slot)
    elif da_generare:
        slot.empty()   # toglie l'avviso se la generazione non ha prodotto nulla


def _render_distribution_and_correlations(insights: dict, sel_measure) -> None:
    dist_fig = st.session_state.get("dist_fig")
    corr_fig = st.session_state.get("corr_fig")
    if dist_fig is None and corr_fig is None:
        return

    cols = st.columns(2 if (dist_fig is not None and corr_fig is not None) else 1)
    idx = 0
    if dist_fig is not None:
        with cols[idx]:
            st.markdown(f"**Distribuzione di {sel_measure}**")
            st.plotly_chart(dist_fig, width="stretch", key="report_dist")
        idx += 1
    if corr_fig is not None:
        with cols[idx]:
            st.markdown("**Correlazioni tra le misure**")
            pairs = insights.get("corr_pairs") or []
            if pairs:
                a, b, r = pairs[0]
                direction = "positiva" if r > 0 else "negativa"
                st.caption(f"Coppia più correlata: {a} e {b} (r = {fmt_num(r)}, {direction}). "
                           "La correlazione indica associazione, non causa.")
            else:
                st.caption("Nessuna coppia con correlazione forte (|r| ≥ 0,6).")
            st.plotly_chart(corr_fig, width="stretch", key="report_corr")


def render_executive_report(agent: DataAgent, insights: dict, limits: DemoLimits,
                            exec_sig: tuple, unit: str) -> None:
    st.subheader("Report esecutivo")
    st.caption("Executive Summary, Key Insights, Recommendations, Risks e Next Steps, "
               "basati sui numeri calcolati dai dati caricati.")

    # Il report resta valido finché non cambiano dati, misura/categoria, provider o unità.
    if st.session_state.get("exec_report") and st.session_state.get("exec_report_sig") != exec_sig:
        st.session_state.exec_report = None

    if st.button("Genera report esecutivo", disabled=not insights.get("text")):
        if demo_allows(limits, "generazioni"):
            with st.spinner("L'AI sta redigendo il report esecutivo..."):
                st.session_state.exec_report = agent.executive_report(
                    with_unit(insights["text"], unit))
                st.session_state.exec_report_sig = exec_sig
            # La narrativa non solleva: se il modello è irraggiungibile restituisce
            # un avviso in corsivo. In quel caso non è stato speso nulla.
            if not str(st.session_state.exec_report).startswith("_(Impossibile"):
                demo_consume(limits)

    if st.session_state.get("exec_report"):
        with st.container(border=True):
            st.markdown(md_safe(st.session_state.exec_report))
        st.download_button("Scarica il report (.md)", st.session_state.exec_report,
                           file_name="report_esecutivo.md", mime="text/markdown")


_FREQ_LABELS = {"Trimestre": "trimestre", "Mese": "mese", "Anno": "anno"}


def render_period_comparison(df: pd.DataFrame, sel_measure, unit: str = "",
                             report_sig: tuple = ()) -> None:
    """
    Confronto di una misura tra periodi (mese/trimestre/anno), deterministico:
    stessi numeri che il modello otterrebbe via `compare_periods`, ma senza LLM.
    Compare solo se il dataset ha almeno una colonna data e una misura numerica.

    `report_sig` identifica i dati su cui si sta lavorando (file + filtro) ed entra
    nella chiave con cui il risultato viene tenuto da parte: vedi sotto.
    """
    date_cols = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    measures = ordered_measures(measure_columns(df))
    if not date_cols or not measures:
        return

    st.subheader("Confronto tra periodi")
    with st.expander("Confronta una misura nel tempo, con la variazione percentuale"):
        c1, c2, c3 = st.columns(3)
        date_col = c1.selectbox("Colonna data", date_cols, key="cmp_date")
        idx = measures.index(sel_measure) if sel_measure in measures else 0
        measure = c2.selectbox("Misura", measures, index=idx, key="cmp_measure")
        freq_label = c3.selectbox("Periodo", list(_FREQ_LABELS), key="cmp_freq")

        # Streamlit esegue il corpo di un expander anche quando è CHIUSO, e
        # compare_periods è un groupby sull'intero dataset: senza memoria si
        # ripagherebbe a ogni rerun (misurati 23 ms sul dataset di esempio, 144 ms
        # su un milione di righe) per un risultato che spesso non è nemmeno in
        # vista. La funzione è pura, quindi basta rifarla quando cambia un
        # ingresso: i dati (report_sig) o una delle tre scelte.
        chiave = (report_sig, date_col, measure, freq_label)
        if st.session_state.get("cmp_key") != chiave:
            st.session_state.cmp_key = chiave
            try:
                st.session_state.cmp_out = (
                    compare_periods(df, date_col, measure, freq=_FREQ_LABELS[freq_label]), None)
            except Exception as e:  # noqa: BLE001 — dati dell'utente: si spiega, non si esplode
                # Anche il fallimento si tiene da parte: rifare il calcolo a ogni
                # rerun per riottenere lo stesso errore non serve a nessuno.
                st.session_state.cmp_out = (None, str(e))

        comp, errore = st.session_state.cmp_out
        if errore is not None:
            st.caption(f"Confronto non disponibile: {errore}")
            return

        # Tabella leggibile: numeri formattati e variazione con segno (— sul primo).
        display = comp.copy()
        display[measure] = display[measure].map(fmt_num)
        display["variazione_%"] = display["variazione_%"].map(
            lambda v: "—" if pd.isna(v) else f"{v:+.1f}%")
        etichetta = measure + (f" ({unit})" if unit else "")
        display = display.rename(columns={measure: etichetta, "variazione_%": "Variazione",
                                          "periodo": "Periodo"})
        st.dataframe(display, width="stretch", hide_index=True)

        # Barre in ordine CRONOLOGICO: px.bar non riordina, mentre to_chart ordina
        # per valore — perderebbe la sequenza temporale, che è il punto del confronto.
        # Unico grafico costruito con px grezzo, quindi l'unico che il tema se lo
        # deve applicare qui: tutti gli altri arrivano già tematizzati.
        st.plotly_chart(apply_theme(px.bar(comp, x="periodo", y=measure)),
                        width="stretch", key="cmp_chart")


# Storico della conversazione. Vive in st.session_state e ogni turno può contenere
# una figura Plotly (che pesa): senza tetto crescerebbe finché la sessione non si
# ricarica, e Streamlit ridisegna TUTTA la cronologia a ogni rerun (ogni click).
_MAX_TURNI = 20        # oltre questo, i turni più vecchi vengono scartati
_TURNI_IN_VISTA = 3    # i più recenti restano aperti; il resto va in un expander


def _cap_storico(turns: list[Turn]) -> list[Turn]:
    """Tiene solo gli ultimi _MAX_TURNI turni, così memoria e costo di render
    restano limitati indipendentemente da quanto a lungo dura la sessione."""
    return turns[-_MAX_TURNI:]


def _render_turn(turn: Turn, key_index: int, columns=None) -> None:
    """Un turno dello storico: la domanda dell'utente e la risposta dell'assistente."""
    with st.chat_message("user"):
        st.write(turn.question)
    with st.chat_message("assistant"):
        render_result(turn.code, turn.result, turn.explanation,
                      kp=f"h{key_index}", columns=columns, question=turn.question)


def _render_turn_streaming(service: AnalysisService, question: str, turn: Turn,
                           columns, unit: str, explain: bool) -> Turn:
    """
    Rende il turno appena chiesto con la spiegazione in STREAMING (effetto typewriter)
    e restituisce il turno completato col testo finale, da conservare nello storico.

    La spiegazione si streamma qui; il resto (avvisi, grafico, dati, codice) passa da
    `render_result` con `explanation=None`, così l'ordine è lo stesso dello storico.
    """
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        testo = None
        if explain and isinstance(turn.result, ExecutionSuccess):
            reso = st.write_stream(service.stream_explanation(question, turn.result, unit))
            # Lo streaming ha reso i `$` come `\$` per non farli diventare LaTeX; nel
            # testo CONSERVATO li riporta a `$`, perché il riquadro dello storico
            # (answer_card) è HTML e mostrerebbe il backslash in chiaro.
            testo = (str(reso).replace("\\$", "$").strip() or None)
        render_result(turn.code, turn.result, explanation=None, kp="live",
                      columns=columns, question=question)
    return replace(turn, explanation=testo)


def _esempi_domande(sel_measure: str | None, sel_category: str | None) -> list[str]:
    """
    Domande d'esempio per l'empty-state della chat, costruite sulle colonne già
    scelte per il report: così i suggerimenti sono sempre pertinenti al dataset
    caricato, non frasi fisse buone solo per la demo. Ripiego su due domande
    generiche quando il dataset non offre né misura né categoria.
    """
    esempi: list[str] = []
    if sel_measure and sel_category:
        esempi.append(f"Mostrami {sel_measure} per {sel_category}")
        esempi.append(f"Quali sono i 5 {sel_category} con più {sel_measure}?")
    if sel_measure:
        esempi.append(f"Qual è il totale di {sel_measure}?")
    elif sel_category:
        esempi.append(f"Quante righe per ciascun {sel_category}?")
    return esempi[:3] or ["Quante righe ha il dataset?", "Mostrami le prime 10 righe"]


# Domande che un valutatore fa davvero, e che la documentazione copre bene: fanno
# da vetrina alle sezioni piu' interessanti invece di lasciare il campo vuoto.
_ESEMPI_PROGETTO = (
    "Come funziona la sandbox di sicurezza?",
    "Come evitate che l'AI inventi i numeri?",
    "Che design pattern sono stati usati?",
)


def render_chat(service: AnalysisService, df: pd.DataFrame, limits: DemoLimits,
                explain: bool, unit: str, dataset_label: str = "",
                sel_measure: str | None = "", sel_category: str | None = "") -> None:
    """Box domanda e storico della conversazione (turno più recente in alto)."""
    st.subheader("Fai una domanda ai tuoi dati")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    with st.form("ask_form", clear_on_submit=True):
        # Input a tutta larghezza + bottone sotto: nella colonna stretta della chat
        # un bottone affiancato diventava troppo stretto e "Invia" andava a capo lettera
        # per lettera. In verticale regge qualsiasi larghezza di colonna.
        user_q = st.text_input(
            "domanda", label_visibility="collapsed", key="chat_q",
            placeholder="Es. 'Qual è il mese con più vendite?' "
                        "oppure 'Mostrami le vendite per regione'",
        )
        submitted = st.form_submit_button("Invia", width="stretch")

    # Slot in cima (creato ORA, riempito DOPO): la risposta del turno appena chiesto
    # si streamma qui, sopra lo storico. Non serve rerun né si rischia il doppio
    # render, perché lo storico si legge PRIMA di aggiungere il nuovo turno.
    live = st.container()

    turns: list[Turn] = st.session_state.messages

    # Esporta l'intera conversazione (numeri, spiegazioni e codice) in Markdown.
    if turns:
        st.download_button(
            "⬇ Scarica la conversazione (.md)",
            data=conversation_to_markdown(turns, dataset_label=dataset_label),
            file_name="conversazione.md", mime="text/markdown",
        )

    # Storico: i più recenti in alto e aperti; i precedenti in un expander. L'indice
    # nella lista fa da chiave stabile ai grafici.
    colonne = list(df.columns)
    indici = list(reversed(range(len(turns))))  # dal più recente al più vecchio
    for i in indici[:_TURNI_IN_VISTA]:
        _render_turn(turns[i], i, columns=colonne)
    vecchi = indici[_TURNI_IN_VISTA:]
    if vecchi:
        with st.expander(f"Conversazioni precedenti ({len(vecchi)})"):
            for i in vecchi:
                _render_turn(turns[i], i, columns=colonne)

    # La domanda da analizzare arriva dal form OPPURE da un suggerimento cliccato.
    question = user_q.strip() if (submitted and user_q and user_q.strip()) else None

    # Empty-state: finché non c'è storico la colonna chat sarebbe quasi vuota. La
    # riempiamo con domande d'esempio pertinenti al dataset — dicono subito che tipo
    # di richiesta funziona, e cliccarne una equivale a scriverla e inviarla.
    if not turns:
        st.caption("Non sai da dove iniziare? Prova una di queste 👇")
        for j, ex in enumerate(_esempi_domande(sel_measure, sel_category)):
            if st.button(ex, key=f"esempio_{j}", width="stretch"):
                question = ex

    if question and demo_allows(limits, "domande"):
        with live:
            # Il codice si genera ed esegue "dietro" lo status (on_step aggiorna la
            # fase); la SPIEGAZIONE si streamma poi in chiaro. explain=False al service:
            # la narrativa non la fa lui, la streamma la UI.
            with st.status("Analisi in corso…", expanded=False) as status:
                turn = service.answer(question, df, explain=False, unit=unit,
                                      on_step=lambda msg: status.update(label=msg))
                status.update(label="Analisi completata", state="complete")
            # Un provider irraggiungibile non ha consumato token: addebitarlo
            # significherebbe far pagare all'utente un guasto nostro.
            if not (isinstance(turn.result, ExecutionFailure) and turn.result.kind == "provider"):
                demo_consume(limits)
            turn = _render_turn_streaming(service, question, turn, colonne, unit, explain)
        st.session_state.messages = _cap_storico(turns + [turn])

def render_project_chat(agent: DataAgent, limits: DemoLimits) -> None:
    """
    "Chiedi al progetto": domande sul lavoro, non sui dati.

    Esiste perche' chi VALUTA questo progetto ha domande di natura diversa da chi
    lo USA ("come funziona la sandbox?" invece di "qual e' il mese migliore?"), e
    finora l'unico modo di rispondergli era che io fossi presente o che leggesse
    quattordicimila parole di documentazione.

    ## Perche' una bolla flottante e non una scheda

    Prima era una scheda accanto alla chat sui dati, e su un portatile finiva
    duemila pixel piu' in basso — dopo tutto il report, perche' sotto la soglia
    responsive le colonne si impilano. Un assistente che si trova solo scorrendo
    non viene trovato. Ancorata al viewport, la bolla e' raggiungibile da qualunque
    punto della pagina e a qualunque risoluzione, ed e' il gesto che chi arriva da
    un sito qualsiasi conosce gia'.

    `st.popover` regge il caso: SOPRAVVIVE al rerun che segue l'invio della
    domanda, quindi la risposta appare nel pannello ancora aperto. Un pannello
    costruito a mano in HTML non potrebbe contenere widget Streamlit — quindi ne'
    il campo di testo ne' la risposta.

    Condivide la QUOTA della demo con la chat sui dati: sono chiamate allo stesso
    modello, sulla stessa chiave, e tenere due budget separati significherebbe
    raddoppiare il tetto di spesa senza dirlo.
    """
    # Il `st.container()` NON e' decorativo: e' l'elemento che il CSS ancora al
    # viewport. Senza di esso il marker finisce nel blocco verticale della PAGINA —
    # e allora il selettore o non trova nulla, o rende `position: fixed` l'intera
    # pagina (larga 1369px, ancorata in basso a destra). Il contenitore da' alla
    # bolla un blocco tutto suo, che e' l'unica cosa che il CSS deve spostare.
    with st.container():
        st.markdown("<span class='bolla-progetto'></span>", unsafe_allow_html=True)
        with st.popover("💬", help="Chiedi al progetto: architettura, sicurezza, "
                                   "scelte tecniche"):
            _project_chat_body(agent, limits)


def _project_chat_body(agent: DataAgent, limits: DemoLimits) -> None:
    """Contenuto del pannello. Separato dal guscio per poterlo leggere senza il CSS."""
    st.markdown("### Chiedi al progetto")
    # Didascalia corta di proposito: nel pannello ogni riga è spazio tolto alla
    # conversazione, e in monospazio (il tema) quattro righe ne mangiavano un quinto.
    st.caption("Risposte dalla documentazione del repo, con la fonte citata.")

    if "project_qa" not in st.session_state:
        st.session_state.project_qa = []

    with st.form("project_form", clear_on_submit=True):
        q = st.text_input("domanda sul progetto", label_visibility="collapsed",
                          key="project_q",
                          placeholder="Es. 'Come funziona la sandbox?' oppure "
                                      "'Perche' Streamlit e non Flask?'")
        inviata = st.form_submit_button("Chiedi", width="stretch")

    live = st.container()
    storico = st.session_state.project_qa

    domanda = q.strip() if (inviata and q and q.strip()) else None

    # I suggerimenti vivono in un SEGNAPOSTO, non scritti direttamente nella pagina.
    # Streamlit riporta il click di un bottone al rerun SUCCESSIVO a quello in cui il
    # bottone è stato disegnato: nel giro in cui si risponde, i suggerimenti sono
    # quindi già a schermo e resterebbero sopra la risposta. Potendo svuotare il
    # segnaposto, spariscono nello stesso istante in cui non servono più.
    suggerimenti = st.empty()
    if not storico:
        with suggerimenti.container():
            st.caption("Oppure prova una di queste 👇")
            for j, ex in enumerate(_ESEMPI_PROGETTO):
                if st.button(ex, key=f"pq_{j}", width="stretch"):
                    domanda = ex
    if domanda:
        suggerimenti.empty()

    if domanda and demo_allows(limits, "domande"):
        with live, st.spinner("Cerco nella documentazione…"):
            testo, fonti = answer_about_project(agent.provider, domanda)
        # Se il recupero non ha trovato nulla il modello non e' stato chiamato:
        # non c'e' niente da addebitare alla quota.
        if fonti:
            demo_consume(limits)
        st.session_state.project_qa = ([(domanda, testo, [f.citazione for f in fonti])]
                                       + storico)[:_TURNI_IN_VISTA]
        storico = st.session_state.project_qa

    for dom, risp, citazioni in storico:
        with st.chat_message("user"):
            st.markdown(md_safe(dom))
        with st.chat_message("assistant"):
            st.markdown(md_safe(risp))
            if citazioni:
                with st.expander(f"Fonti ({len(citazioni)})"):
                    for c in citazioni:
                        st.caption(f"• {c}")
