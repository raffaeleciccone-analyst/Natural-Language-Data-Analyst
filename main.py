import html
import os

import pandas as pd
import streamlit as st

from core.loader import (load_dataset, read_any, profile, analyze,
                         measure_columns, SUPPORTED_EXTENSIONS)
from core.agent import DataAgent
from core import executor as ex
from core.executor import (execute_pandas_code, is_plotly_figure,
                           summarize_result, apply_theme, to_chart)
from core.providers import available_providers, DEFAULT_MODELS, REQUIRES_API_KEY

# --- Configurazione pagina ---
st.set_page_config(
    page_title="AI Data Analyst Assistant",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY"}


def _secret(key: str, default: str = "") -> str:
    """Legge un valore da st.secrets (deploy) o dalle variabili d'ambiente (locale)."""
    try:
        val = st.secrets.get(key)  # type: ignore[attr-defined]
        if val is not None:
            return str(val)
    except Exception:
        pass
    return os.getenv(key, default)


# Modalità demo (deploy pubblico): attivata dai secrets. Usa provider/modello/chiave
# configurati, nasconde i campi sensibili e limita le domande per contenere i costi.
DEMO_MODE = _secret("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")
DEMO_MAX_QUESTIONS = int(_secret("DEMO_MAX_QUESTIONS", "15") or "15")

# --- Sidebar: configurazione (letta prima di applicare il tema) ---
with st.sidebar:
    st.header("⚙️ Configurazione")

    dark_mode = st.toggle("🌙 Tema scuro", value=False)
    spiega_ai = st.toggle("💡 Spiegazione AI", value=True,
                          help="Genera una risposta testuale che interpreta il risultato.")

    st.divider()
    st.subheader("Modello LLM")
    if DEMO_MODE:
        provider = _secret("PROVIDER", "gemini").strip().lower()
        model_name = _secret("MODEL", DEFAULT_MODELS.get(provider, ""))
        api_key = _secret(_KEY_ENV.get(provider, ""), "")
        st.success(f"🚀 Demo pubblica · **{provider}** · `{model_name}`")
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

# Propaga il tema ai grafici Plotly
ex.set_theme(dark_mode)


# --- Stile personalizzato "Console" (chiaro / scuro) ---
def inject_css(dark: bool):
    if dark:
        c = dict(page="#0d0d0d", surface="#1a1a19", surface2="#202020",
                 border="#2c2c2a", strong="#3a3a38", ink="#ffffff", ink2="#c3c2b7",
                 muted="#898781", accent="#22b0bc", deep="#4cc7d1", tint="#123032")
    else:
        c = dict(page="#eff1ee", surface="#ffffff", surface2="#f7f8f6",
                 border="#e2e6e1", strong="#d3d8d1", ink="#16191c", ink2="#59626b",
                 muted="#8a929a", accent="#0e7c86", deep="#0a5960", tint="#e2f0f0")

    st.markdown(
        f"""
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
          :root {{
            --sans:"IBM Plex Sans",system-ui,-apple-system,sans-serif;
            --display:"Space Grotesk",system-ui,sans-serif;
            --mono:"IBM Plex Mono",ui-monospace,"SF Mono",monospace;
          }}
          .stApp, [data-testid="stAppViewContainer"] {{ background: {c['page']}; }}
          .stApp, .stApp p, .stApp label, .stApp li, .stMarkdown {{
              font-family: var(--sans); color: {c['ink']};
          }}
          .block-container {{ padding-top: 2rem; max-width: 1150px; }}
          section[data-testid="stSidebar"] {{
              background: {c['surface']}; border-right: 1px solid {c['border']};
          }}
          h1, h2, h3, h4 {{ font-family: var(--display); color: {c['ink']}; letter-spacing: -0.015em; }}
          .app-subtitle {{ font-family: var(--mono); color: {c['ink2']} !important;
              font-size: 0.82rem; margin-top: -0.6rem; letter-spacing: 0.02em; }}
          code, .app-subtitle code {{ font-family: var(--mono); color: {c['deep']}; }}
          [data-testid="stCaptionContainer"] {{ font-family: var(--mono); color: {c['muted']}; }}

          /* Readout KPI (firma "console") */
          .readout {{
              background: {c['surface']}; border: 1px solid {c['border']};
              border-radius: 14px; padding: 15px 18px 14px;
              box-shadow: 0 1px 2px rgba(0,0,0,0.05), 0 10px 26px -20px rgba(0,0,0,0.35);
          }}
          .readout .r-k {{ font-family: var(--mono); font-size: 0.66rem; letter-spacing: 0.1em;
              text-transform: uppercase; color: {c['ink2']}; }}
          .readout .r-v {{ font-family: var(--mono); font-weight: 600; font-size: 1.85rem;
              letter-spacing: -0.02em; margin-top: 7px; line-height: 1;
              font-variant-numeric: tabular-nums; color: {c['ink']}; }}
          .readout .r-tick {{ height: 6px; margin-top: 12px; border-radius: 2px;
              background: linear-gradient(90deg, var(--bar,{c['accent']}) 60%, {c['border']} 60%); }}
          .readout .r-sub {{ font-family: var(--mono); font-size: 0.7rem; color: {c['muted']}; margin-top: 8px; }}

          /* Motivo a scala di misurazione */
          .scale {{ height: 10px; margin: 8px 0 2px;
              background-image: repeating-linear-gradient(90deg, {c['strong']} 0 1px, transparent 1px 9px); }}

          /* Riquadro risposta / sintesi */
          .answer-card {{
              background: {c['tint']}; border: 1px solid {c['border']};
              border-left: 3px solid {c['accent']}; border-radius: 13px;
              padding: 15px 18px; margin: 4px 0 14px 0;
          }}
          .answer-label {{
              font-family: var(--mono); color: {c['accent']}; font-weight: 600;
              font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.08em;
              margin-bottom: 7px; display: flex; align-items: center; gap: 8px;
          }}
          .answer-label::before {{ content:""; width:14px; height:2px; background:{c['accent']}; }}
          .answer-body {{ color: {c['ink']}; font-size: 1.0rem; line-height: 1.6; }}
          .answer-body b {{ font-family: var(--mono); color: {c['deep']}; font-weight: 600; }}

          /* Metriche native (fallback), tabelle, chat */
          div[data-testid="stMetricValue"] {{ font-family: var(--mono); color: {c['accent']}; font-weight: 600; }}
          div[data-testid="stMetricLabel"] {{ font-family: var(--mono); color: {c['ink2']}; }}
          div[data-testid="stChatMessage"] {{
              background: {c['surface']}; border: 1px solid {c['border']};
              border-radius: 14px; padding: 6px 14px;
          }}
          .stExpander {{ border-radius: 12px; border: 1px solid {c['border']}; }}
          [data-testid="stChatInput"] textarea {{ font-family: var(--sans); }}
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_css(dark_mode)


def answer_card(label: str, text: str):
    """Renderizza un testo in un riquadro dedicato (con escaping HTML)."""
    safe = html.escape(text).replace("\n", "<br>")
    st.markdown(
        f"<div class='answer-card'>"
        f"<div class='answer-label'>{html.escape(label)}</div>"
        f"<div class='answer-body'>{safe}</div></div>",
        unsafe_allow_html=True,
    )


def readout(col, label: str, value: str, sub: str = "", tick: str = "#0e7c86"):
    """Card KPI in stile 'console': valore monospazio + tacca colorata."""
    sub_html = f"<div class='r-sub'>{html.escape(sub)}</div>" if sub else ""
    col.markdown(
        f"<div class='readout'>"
        f"<div class='r-k'>{html.escape(label)}</div>"
        f"<div class='r-v'>{html.escape(value)}</div>"
        f"<div class='r-tick' style='--bar:{tick}'></div>{sub_html}</div>",
        unsafe_allow_html=True,
    )


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

# --- Header ---
st.title("📊 AI Data Analyst Assistant")
st.markdown(
    f"<p class='app-subtitle'>Interroga i tuoi dati in linguaggio naturale — "
    f"provider attivo: <b>{html.escape(provider)}</b> · "
    f"<code>{html.escape(model_name)}</code></p>",
    unsafe_allow_html=True,
)

# --- Caricamento del DataFrame ---
df = None
source_label = None
if uploaded_file is not None:
    try:
        df = read_any(uploaded_file)
        source_label = f"File caricato: {uploaded_file.name}"
    except Exception as e:
        st.error(f"Errore nel caricamento del file: {e}")
else:
    try:
        df = load_dataset()
        source_label = "Dataset di default (Superstore Sales)"
    except FileNotFoundError:
        df = None

if df is None:
    st.info("👈 Carica un file CSV dalla barra laterale per iniziare.")
    st.stop()

st.caption(f"📁 {source_label} — {len(df):,} righe · {df.shape[1]} colonne")

# --- Riga di KPI (readout in stile console) ---
kpi_cols = st.columns(4)
readout(kpi_cols[0], "Righe", f"{len(df):,}", tick="#2a78d6")
readout(kpi_cols[1], "Colonne", f"{df.shape[1]}", tick="#008300")
# Colonne-misura reali (identificatori come Row ID / CAP esclusi), senza duplicati.
measures = measure_columns(df)
priorita = [c for c in ["Sales", "Profit", "Revenue", "Amount", "Total"] if c in measures]
ordinate = priorita + [c for c in measures if c not in priorita]
_kpi_ticks = ["#0e7c86", "#eda100"]
for i, (slot, col) in enumerate(zip(kpi_cols[2:], ordinate[:2])):
    readout(slot, f"Totale {col}", f"{df[col].sum():,.0f}", tick=_kpi_ticks[i])

# --- Anteprima dati ---
with st.expander("👀 Anteprima dei dati (prime 10 righe)"):
    st.dataframe(df.head(10), use_container_width=True)

# --- Report iniziale sui dati (rigenerato al cambio di dataset per CONTENUTO) ---
try:
    content_hash = int(pd.util.hash_pandas_object(df, index=False).sum())
except Exception:
    content_hash = None
dataset_sig = (source_label, df.shape, tuple(df.columns), content_hash)

if st.session_state.get("dataset_sig") != dataset_sig:
    st.session_state.dataset_sig = dataset_sig
    st.session_state.messages = []  # nuova sorgente dati -> nuova conversazione

    with st.spinner("Analisi del dataset in corso..."):
        st.session_state.profile = profile(df)
        insights = analyze(df)
        st.session_state.insights = insights

        # Grafici del report
        top_fig = trend_fig = None
        if "top" in insights:
            _, _, top_df = insights["top"]
            try:
                top_fig = to_chart(top_df, kind="bar")
            except Exception:
                top_fig = None
        if "trend" in insights:
            _, _, per_df = insights["trend"]
            try:
                trend_fig = to_chart(per_df, kind="line")
            except Exception:
                trend_fig = None
        st.session_state.top_fig = top_fig
        st.session_state.trend_fig = trend_fig

insights = st.session_state.get("insights", {})

# Narrativa AI: rigenerata anche al cambio di provider/modello o del toggle spiegazioni
overview_sig = (dataset_sig, spiega_ai, provider, model_name)
if st.session_state.get("overview_sig") != overview_sig:
    st.session_state.overview_sig = overview_sig
    if spiega_ai and insights.get("text"):
        with st.spinner("L'AI sta preparando la sintesi dei dati..."):
            st.session_state.overview_text = agent.overview(insights["text"])
    else:
        st.session_state.overview_text = None

st.markdown("<div class='scale'></div>", unsafe_allow_html=True)
st.subheader("📋 Report iniziale sui dati")

# Narrativa AI con i numeri chiave
if st.session_state.get("overview_text"):
    answer_card("📊 Sintesi dei dati", st.session_state.overview_text)

# Statistiche numeriche
if "numeric_stats" in insights:
    st.markdown("**Statistiche delle colonne numeriche**")
    st.dataframe(insights["numeric_stats"], use_container_width=True, hide_index=True)

# Grafici: classifica principale e andamento temporale
top_fig = st.session_state.get("top_fig")
trend_fig = st.session_state.get("trend_fig")
if top_fig is not None or trend_fig is not None:
    graf_cols = st.columns(2 if (top_fig is not None and trend_fig is not None) else 1)
    idx = 0
    if top_fig is not None:
        cat, num, _ = insights["top"]
        with graf_cols[idx]:
            st.markdown(f"**Classifica: {num} per {cat}**")
            st.plotly_chart(apply_theme(top_fig), use_container_width=True)
        idx += 1
    if trend_fig is not None:
        dcol, num, _ = insights["trend"]
        with graf_cols[idx]:
            st.markdown(f"**Andamento di {num} nel tempo**")
            st.plotly_chart(apply_theme(trend_fig), use_container_width=True)

with st.expander("🔎 Struttura delle colonne (tipi, mancanti, valori)"):
    st.dataframe(st.session_state.get("profile"), use_container_width=True, hide_index=True)

st.markdown("<div class='scale'></div>", unsafe_allow_html=True)
st.subheader("💬 Fai una domanda ai tuoi dati")


# --- Rendering di un risultato ---
def render_result(code: str, result, explanation: str | None = None):
    # 1. Risposta testuale (in un riquadro dedicato)
    if explanation:
        answer_card("📝 Risposta", explanation)

    # 2. Risultato visuale
    if is_plotly_figure(result):
        st.plotly_chart(apply_theme(result), use_container_width=True)
    elif isinstance(result, pd.DataFrame):
        st.dataframe(result, use_container_width=True)
    elif isinstance(result, pd.Series):
        st.dataframe(result.rename("valore").to_frame(), use_container_width=True)
    elif isinstance(result, str) and result.startswith("Errore"):
        st.error(result)
    elif isinstance(result, (int, float)):
        st.metric("Risultato", f"{result:,.2f}" if isinstance(result, float) else f"{result:,}")
    elif isinstance(result, str) and "Grafico generato" in result:
        st.success(result)
    else:
        st.info(f"**Risultato:** {result}")

    # 3. Codice generato (in fondo, collassato)
    with st.expander("⚙️ Codice Pandas generato"):
        st.code(code, language="python")


# --- Storico conversazione ---
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.write(msg["content"])
        else:
            render_result(msg["code"], msg["result"], msg.get("explanation"))

# --- Nuova domanda ---
# "mostrami"/"visualizza" erano troppo generiche (attivavano il grafico anche su
# richieste scalari). Teniamo solo parole realmente grafiche.
PAROLE_GRAFICO = ["grafico", "plot", "barre", "linee", "andamento", "istogramma",
                  "trend", "distribuzione", "diagramma"]
PAROLE_LINEA = ["andamento", "linee", "trend", "tempo", "temporale"]

prompt = st.chat_input("Es. 'Qual è il mese con più vendite?' oppure 'Mostrami le vendite per regione'")

if prompt and prompt.strip() and DEMO_MODE and \
        st.session_state.get("_demo_q", 0) >= DEMO_MAX_QUESTIONS:
    st.warning(f"Hai raggiunto il limite della demo ({DEMO_MAX_QUESTIONS} domande). "
               "Clona il repo da GitHub per uso illimitato. Grazie per aver provato! 🙌")
    prompt = None

if prompt and prompt.strip():
    if DEMO_MODE:
        st.session_state["_demo_q"] = st.session_state.get("_demo_q", 0) + 1
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        richiede_grafico = any(p in prompt.lower() for p in PAROLE_GRAFICO)
        kind = "line" if any(p in prompt.lower() for p in PAROLE_LINEA) else "bar"

        def wrap_chart(codice: str) -> str:
            """Se serviva un grafico ma il modello non l'ha prodotto, lo costruiamo con to_chart."""
            if (richiede_grafico and "fig" not in codice
                    and "px." not in codice and "st." not in codice):
                base = codice.strip().rstrip(";")
                return f"fig = to_chart({base}, kind='{kind}')"
            return codice

        domanda = prompt + (" (Raggruppa i dati usando as_index=False)" if richiede_grafico else "")

        with st.spinner("L'AI sta generando il codice..."):
            codice = wrap_chart(agent.ask_code(domanda, df))

        with st.spinner("Esecuzione sui dati..."):
            risultato = execute_pandas_code(codice, df)

        # Retry automatico: se l'esecuzione fallisce, rimandiamo l'errore all'LLM
        MAX_RETRY = 2
        tentativo = 0
        while (isinstance(risultato, str) and risultato.startswith("Errore")
               and tentativo < MAX_RETRY):
            tentativo += 1
            with st.spinner(f"Correzione automatica del codice (tentativo {tentativo})..."):
                codice = wrap_chart(agent.fix_code(domanda, df, codice, risultato))
                risultato = execute_pandas_code(codice, df)
        if tentativo and not (isinstance(risultato, str) and risultato.startswith("Errore")):
            st.caption(f"✅ Codice corretto automaticamente dopo {tentativo} tentativo/i.")

        # Spiegazione testuale (seconda chiamata LLM), se abilitata e il calcolo è andato a buon fine
        spiegazione = None
        calcolo_ok = not (isinstance(risultato, str) and risultato.startswith("Errore"))
        if spiega_ai and calcolo_ok:
            with st.spinner("L'AI sta interpretando il risultato..."):
                spiegazione = agent.explain(prompt, summarize_result(risultato))

        render_result(codice, risultato, spiegazione)

    st.session_state.messages.append(
        {"role": "assistant", "code": codice, "result": risultato, "explanation": spiegazione}
    )
