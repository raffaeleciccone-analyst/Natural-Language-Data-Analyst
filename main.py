import html
import io
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from core.agent import DataAgent
from core.executor import apply_theme, corr_heatmap, histogram, to_chart
from core.loader import (
    SUPPORTED_EXTENSIONS,
    analyze,
    best_category,
    category_columns,
    load_dataset,
    measure_columns,
    profile,
    read_any,
)
from core.log import get_logger
from core.providers import DEFAULT_MODELS, REQUIRES_API_KEY, available_providers
from core.service import AnalysisService, Turn
from core.ui_components import answer_card, build_kpis, readout, render_linked_charts, render_result
from core.ui_theme import console_css
from core.utils import fmt_num

log = get_logger(__name__)

# --- Configurazione pagina ---
# Favicon: logo del progetto (PNG trasparente in assets/); ripiego sull'emoji se
# il file non è presente, così l'app parte comunque.
_FAVICON = Path(__file__).parent / "assets" / "favicon.png"
_PAGE_ICON = str(_FAVICON) if _FAVICON.exists() else "📊"
st.set_page_config(
    page_title="Natural Language Data Analyst",
    page_icon=_PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY", "groq": "GROQ_API_KEY"}


def _secret(key: str, default: str = "") -> str:
    """Legge un valore da st.secrets (deploy) o dalle variabili d'ambiente (locale)."""
    try:
        val = st.secrets.get(key)
        if val is not None:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, default)


# Modalità demo (deploy pubblico): attivata dai secrets. Usa provider/modello/chiave
# configurati, nasconde i campi sensibili e limita le domande per contenere i costi.
DEMO_MODE = _secret("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")
DEMO_MAX_QUESTIONS = int(_secret("DEMO_MAX_QUESTIONS", "15") or "15")


def demo_allows(kind: str) -> bool:
    """In demo: True se resta budget (e incrementa il contatore); altrimenti mostra il limite."""
    if not DEMO_MODE:
        return True
    if st.session_state.get("_demo_q", 0) >= DEMO_MAX_QUESTIONS:
        st.warning(f"Hai raggiunto il limite della demo ({DEMO_MAX_QUESTIONS} {kind}). "
                   "Clona il repo da GitHub per uso illimitato.")
        return False
    st.session_state["_demo_q"] = st.session_state.get("_demo_q", 0) + 1
    return True


def _with_unit(text: str, unit: str) -> str:
    """Antepone al testo l'unità di misura, se l'utente ne ha indicata una (per l'LLM)."""
    return f"L'unità di misura dei valori è '{unit}'.\n{text}" if unit else text


# --- Sidebar: configurazione (letta prima di applicare il tema) ---
with st.sidebar:
    st.header("Configurazione")

    spiega_ai = st.toggle("Spiegazione AI", value=True,
                          help="Genera una risposta testuale che interpreta il risultato.")

    st.divider()
    st.subheader("Modello LLM")
    if DEMO_MODE:
        provider = _secret("PROVIDER", "groq").strip().lower()
        model_name = _secret("MODEL", DEFAULT_MODELS.get(provider, ""))
        api_key = _secret(_KEY_ENV.get(provider, ""), "")
        st.success(f"Demo pubblica · **{provider}** · `{model_name}`")
        st.caption(f"Limite: {DEMO_MAX_QUESTIONS} domande per sessione. "
                   "Clona il repo per uso illimitato e per usare Ollama in locale.")
    else:
        provider = st.selectbox(
            "Provider",
            available_providers(),
            help="Ollama gira in locale; Anthropic, OpenAI e Gemini richiedono una API key.",
        )
        model_name = st.text_input("Modello", value=DEFAULT_MODELS[provider])
        api_key = ""
        if provider in REQUIRES_API_KEY:
            api_key = st.text_input(
                "API Key",
                type="password",
                help="Lascia vuoto per usare la variabile d'ambiente "
                     "(ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY).",
            )
            if not api_key:  # ripiego su secret/env
                api_key = _secret(_KEY_ENV.get(provider, ""), "")

    st.divider()
    st.subheader("Dataset")
    uploaded_file = st.file_uploader(
        "Carica un file",
        type=SUPPORTED_EXTENSIONS,
        help="Formati supportati: CSV, Excel (.xlsx/.xls), JSON.",
    )

# --- Stile personalizzato "Console" (definito in core/ui_theme.py) ---
st.markdown(console_css(), unsafe_allow_html=True)


# --- Inizializzazione dell'agente (per-sessione, non in cache globale) ---
# Nota di sicurezza: NON usiamo st.cache_resource, che è una cache condivisa fra
# tutte le sessioni del server: la API key finirebbe in memoria globale. Teniamo
# l'agente in st.session_state (isolato per sessione) e lo ricreiamo se cambia la config.
def get_agent(provider: str, model_name: str, api_key: str) -> DataAgent:
    key = (provider, model_name or None, api_key or None)
    if st.session_state.get("_agent_key") != key:
        st.session_state["_agent_key"] = key
        st.session_state["_agent"] = DataAgent(
            provider=provider, model_name=model_name or None, api_key=api_key or None,
        )
    return st.session_state["_agent"]


agent = get_agent(provider, model_name, api_key)
# L'orchestrazione della domanda vive nel servizio (testabile senza Streamlit);
# qui resta solo il flusso della pagina.
service = AnalysisService(agent)

# --- Header ---
st.title("Natural Language Data Analyst")
st.markdown(
    f"<p class='app-subtitle'>Interroga i tuoi dati in linguaggio naturale — "
    f"provider attivo: <b>{html.escape(provider)}</b> · "
    f"<code>{html.escape(model_name)}</code></p>",
    unsafe_allow_html=True,
)

# --- Caricamento del DataFrame (con cache) ---
# Streamlit ri-esegue l'intero script a ogni interazione: senza cache il file
# verrebbe riletto e ri-parsato a ogni click. @st.cache_data memoizza sul CONTENUTO
# (nome + byte), quindi la rilettura avviene solo quando il file cambia davvero.
class _NamedBytesIO(io.BytesIO):
    """BytesIO con un attributo `.name`, così read_any riconosce l'estensione."""
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


@st.cache_data(show_spinner=False)
def load_uploaded_cached(name: str, data: bytes) -> pd.DataFrame:
    return read_any(_NamedBytesIO(data, name))


@st.cache_data(show_spinner=False)
def load_default_cached() -> pd.DataFrame:
    return load_dataset()


df = None
source_label = None
if uploaded_file is not None:
    try:
        df = load_uploaded_cached(uploaded_file.name, uploaded_file.getvalue())
        source_label = f"File caricato: {uploaded_file.name}"
    except Exception as e:
        st.error(f"Errore nel caricamento del file: {e}")
else:
    try:
        df = load_default_cached()
        source_label = "Dataset di default (Superstore Sales)"
    except FileNotFoundError:
        df = None

if df is None:
    st.info("Carica un file CSV dalla barra laterale per iniziare.")
    st.stop()

st.caption(f"{source_label} — {fmt_num(len(df))} righe · {df.shape[1]} colonne")

# --- Selettori del report (sidebar): misura e categoria, con default euristico ---
_measures = measure_columns(df)
_priorita = [c for c in ["Sales", "Profit", "Revenue", "Amount", "Total"] if c in _measures]
_ordinate = _priorita + [c for c in _measures if c not in _priorita]
_cats = category_columns(df)
with st.sidebar:
    st.divider()
    st.subheader("Report")
    if _ordinate:
        sel_measure = st.selectbox("Misura", _ordinate, index=0,
                                   help="La colonna numerica su cui basare KPI e classifiche.")
    else:
        sel_measure = None
        st.caption("Nessuna colonna numerica: report a conteggi.")
    if _cats:
        _def_cat = best_category(df)
        _cat_idx = _cats.index(_def_cat) if _def_cat in _cats else 0
        sel_category = st.selectbox("Categoria", _cats, index=_cat_idx,
                                    help="La dimensione per classifiche e filtri.")
    else:
        sel_category = None
    unit = st.text_input("Unità di misura (opzionale)", value="",
                         placeholder="es. €, kg, %, unità",
                         help="Mostrata accanto ai valori nei KPI e nelle risposte. "
                              "Per misure economiche senza unità si usa $ come standard.").strip()

# Dataset economico senza unità indicata dall'utente → dollaro come standard.
_ECON_HINTS = ("sales", "revenue", "profit", "amount", "price", "cost", "income",
               "expense", "budget", "margin", "fatturato", "vendite", "ricavi",
               "costo", "prezzo", "importo", "spesa", "utile", "margine", "incasso")
if not unit and sel_measure and any(h in sel_measure.lower() for h in _ECON_HINTS):
    unit = "$"


# --- Riga di KPI (adattivi: misure oppure conteggi) ---
kpis = build_kpis(df, sel_measure, sel_category, unit)
kpi_cols = st.columns(len(kpis))
for _col, (_lab, _val, _sub, _tick, _small) in zip(kpi_cols, kpis, strict=False):
    readout(_col, _lab, _val, sub=_sub, tick=_tick, small=_small)

st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

# --- Anteprima dati ---
with st.expander("Anteprima dei dati (prime 10 righe)"):
    st.dataframe(df.head(10), width="stretch")

# --- Report iniziale sui dati ---
def _try_fig(fn, *args, **kwargs):
    """Costruisce una figura, o None se qualcosa va storto (report robusto).
    L'errore viene loggato (non mostrato all'utente) per restare diagnosticabile."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        log.warning("Figura del report non generata da %s: %s", getattr(fn, "__name__", fn), e)
        return None


content_hash: int | None
try:
    content_hash = int(pd.util.hash_pandas_object(df, index=False).sum())
except Exception:
    content_hash = None
dataset_sig = (source_label, df.shape, tuple(df.columns), content_hash)

# Cambio di sorgente dati -> nuova conversazione + profilo
if st.session_state.get("dataset_sig") != dataset_sig:
    st.session_state.dataset_sig = dataset_sig
    st.session_state.messages = []
    st.session_state.profile = profile(df)

# Report vero e proprio: rigenerato anche al cambio di misura/categoria selezionate
report_sig = (dataset_sig, sel_measure, sel_category)
if st.session_state.get("report_sig") != report_sig:
    st.session_state.report_sig = report_sig
    with st.spinner("Analisi del dataset in corso..."):
        insights = analyze(df, measure=sel_measure, category=sel_category)
        st.session_state.insights = insights

        st.session_state.top_fig = (
            _try_fig(to_chart, insights["top"].data, kind="bar") if "top" in insights else None)
        st.session_state.trend_fig = (
            _try_fig(to_chart, insights["trend"].data, kind="line") if "trend" in insights else None)
        st.session_state.corr_fig = (
            _try_fig(corr_heatmap, insights["corr"]) if "corr" in insights else None)
        st.session_state.dist_fig = (
            _try_fig(histogram, df, sel_measure) if sel_measure else None)

insights = st.session_state.get("insights", {})

# Narrativa AI: rigenerata anche al cambio di provider/modello/toggle/unità
overview_sig = (report_sig, spiega_ai, provider, model_name, unit)
if st.session_state.get("overview_sig") != overview_sig:
    st.session_state.overview_sig = overview_sig
    if spiega_ai and insights.get("text"):
        with st.spinner("L'AI sta preparando la sintesi dei dati..."):
            st.session_state.overview_text = agent.overview(_with_unit(insights["text"], unit))
    else:
        st.session_state.overview_text = None

st.markdown("<div class='scale'></div>", unsafe_allow_html=True)
st.subheader("Report iniziale sui dati")

# Narrativa AI con i numeri chiave
if st.session_state.get("overview_text"):
    answer_card("Sintesi dei dati", st.session_state.overview_text)

# Insight automatici (numeri calcolati in Pandas, non dedotti dall'AI).
# Via answer_card: il testo è HTML-escaped, così un valore di cella ostile non
# può iniettare Markdown/HTML nella pagina.
if insights.get("findings"):
    answer_card("Insight automatici", "\n".join(f"• {f}" for f in insights["findings"]))

# Statistiche numeriche (numeri formattati in modo leggibile)
if "numeric_stats" in insights:
    st.markdown("**Statistiche delle colonne numeriche**")
    stats_disp = insights["numeric_stats"].copy()
    for _c in ["Somma", "Media", "Minimo", "Massimo"]:
        if _c in stats_disp.columns:
            stats_disp[_c] = stats_disp[_c].map(fmt_num)
    st.dataframe(stats_disp, width="stretch", hide_index=True)

render_linked_charts(df, insights, st.session_state.get("top_fig"),
                     st.session_state.get("trend_fig"))

# --- Distribuzione della misura e correlazioni (calcolate in Pandas) ---
dist_fig = st.session_state.get("dist_fig")
corr_fig = st.session_state.get("corr_fig")
if dist_fig is not None or corr_fig is not None:
    dc_cols = st.columns(2 if (dist_fig is not None and corr_fig is not None) else 1)
    _di = 0
    if dist_fig is not None:
        with dc_cols[_di]:
            st.markdown(f"**Distribuzione di {sel_measure}**")
            st.plotly_chart(apply_theme(dist_fig), width="stretch", key="report_dist")
        _di += 1
    if corr_fig is not None:
        with dc_cols[_di]:
            st.markdown("**Correlazioni tra le misure**")
            pairs = insights.get("corr_pairs") or []
            if pairs:
                a, b, r = pairs[0]
                verso = "positiva" if r > 0 else "negativa"
                st.caption(f"Coppia più correlata: {a} e {b} (r = {fmt_num(r)}, {verso}). "
                           "La correlazione indica associazione, non causa.")
            else:
                st.caption("Nessuna coppia con correlazione forte (|r| ≥ 0,6).")
            st.plotly_chart(apply_theme(corr_fig), width="stretch", key="report_corr")

with st.expander("Struttura delle colonne (tipi, mancanti, valori)"):
    st.dataframe(st.session_state.get("profile"), width="stretch", hide_index=True)

# --- Report esecutivo (l'AI narra gli insight GIÀ calcolati) ---
st.markdown("<div class='scale'></div>", unsafe_allow_html=True)
st.subheader("Report esecutivo")
st.caption("Executive Summary, Key Insights, Recommendations, Risks e Next Steps, "
           "basati sui numeri calcolati dai dati caricati.")

# Il report resta valido finché non cambiano dati, misura/categoria, provider o unità.
exec_sig = (report_sig, provider, model_name, unit)
if st.session_state.get("exec_report") and st.session_state.get("exec_report_sig") != exec_sig:
    st.session_state.exec_report = None

if st.button("Genera report esecutivo", disabled=not insights.get("text")):
    if demo_allows("generazioni"):
        with st.spinner("L'AI sta redigendo il report esecutivo..."):
            st.session_state.exec_report = agent.executive_report(
                _with_unit(insights["text"], unit))
            st.session_state.exec_report_sig = exec_sig

if st.session_state.get("exec_report"):
    with st.container(border=True):
        st.markdown(st.session_state.exec_report)
    st.download_button("Scarica il report (.md)", st.session_state.exec_report,
                       file_name="report_esecutivo.md", mime="text/markdown")

st.markdown("<div class='scale'></div>", unsafe_allow_html=True)
st.subheader("Fai una domanda ai tuoi dati")


if "messages" not in st.session_state:
    st.session_state.messages = []


def process_question(prompt: str) -> None:
    """Delega il turno al servizio e lo aggiunge allo storico della sessione."""
    with st.spinner("Analisi in corso..."):
        turn = service.answer(prompt, df, explain=spiega_ai, unit=unit)
    st.session_state.messages.append(turn)


# --- Box domanda (inline, integrato nel flusso) ---
with st.form("ask_form", clear_on_submit=True):
    c_in, c_btn = st.columns([8, 1])
    user_q = c_in.text_input(
        "domanda", label_visibility="collapsed",
        placeholder="Es. 'Qual è il mese con più vendite?' oppure 'Mostrami le vendite per regione'",
    )
    submitted = c_btn.form_submit_button("Invia", width="stretch")

if submitted and user_q and user_q.strip():
    if demo_allows("domande"):
        process_question(user_q.strip())

# --- Storico conversazione (turno più recente in alto) ---
# Lo storico è una lista di Turn: domanda e risposta stanno nello stesso oggetto,
# quindi non serve più ricostruire le coppie scorrendo messaggi alternati.
_turns: list[Turn] = st.session_state.messages
for _oi, _turn in reversed(list(enumerate(_turns))):
    with st.chat_message("user"):
        st.write(_turn.question)
    with st.chat_message("assistant"):
        render_result(_turn.code, _turn.result, _turn.explanation, kp=f"h{_oi}")
