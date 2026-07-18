import html

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

# --- Sidebar: configurazione (letta prima di applicare il tema) ---
with st.sidebar:
    st.header("⚙️ Configurazione")

    dark_mode = st.toggle("🌙 Tema scuro", value=False)
    spiega_ai = st.toggle("💡 Spiegazione AI", value=True,
                          help="Genera una risposta testuale che interpreta il risultato.")

    st.divider()
    st.subheader("Modello LLM")
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

    st.divider()
    st.subheader("Dataset")
    uploaded_file = st.file_uploader(
        "Carica un file",
        type=SUPPORTED_EXTENSIONS,
        help="Formati supportati: CSV, Excel (.xlsx/.xls), JSON.",
    )

# Propaga il tema ai grafici Plotly
ex.set_theme(dark_mode)


# --- Stile personalizzato (chiaro / scuro) ---
def inject_css(dark: bool):
    if dark:
        c = dict(page="#0d0d0d", surface="#1a1a19", border="#2c2c2a",
                 ink="#ffffff", secondary="#c3c2b7", accent="#3987e5", tint="#16233d")
    else:
        c = dict(page="#f9f9f7", surface="#ffffff", border="#e1e0d9",
                 ink="#0b0b0b", secondary="#52514e", accent="#2a78d6", tint="#eef4fc")

    st.markdown(
        f"""
        <style>
          .stApp, [data-testid="stAppViewContainer"] {{ background: {c['page']}; }}
          .block-container {{ padding-top: 2.2rem; max-width: 1150px; }}
          section[data-testid="stSidebar"] {{
              background: {c['surface']}; border-right: 1px solid {c['border']};
          }}
          h1, h2, h3, h4 {{ color: {c['ink']}; letter-spacing: -0.01em; }}
          .stApp p, .stApp label, .stApp li, .stMarkdown {{ color: {c['ink']}; }}
          .app-subtitle {{ color: {c['secondary']} !important; font-size: 0.95rem; margin-top: -0.6rem; }}
          code {{ color: {c['accent']}; }}
          div[data-testid="stMetric"] {{
              background: {c['surface']}; border: 1px solid {c['border']};
              border-radius: 14px; padding: 14px 18px;
              box-shadow: 0 1px 2px rgba(0,0,0,0.05);
          }}
          div[data-testid="stMetricValue"] {{ color: {c['accent']}; font-weight: 700; }}
          div[data-testid="stMetricLabel"] {{ color: {c['secondary']}; }}
          div[data-testid="stChatMessage"] {{
              background: {c['surface']}; border: 1px solid {c['border']};
              border-radius: 14px; padding: 6px 14px;
          }}
          .stExpander {{ border-radius: 12px; border: 1px solid {c['border']}; }}
          [data-testid="stCaptionContainer"] {{ color: {c['secondary']}; }}
          /* Riquadro della risposta testuale */
          .answer-card {{
              background: {c['tint']};
              border: 1px solid {c['border']};
              border-left: 4px solid {c['accent']};
              border-radius: 12px;
              padding: 14px 18px;
              margin: 4px 0 14px 0;
          }}
          .answer-label {{
              color: {c['accent']}; font-weight: 700; font-size: 0.72rem;
              text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px;
          }}
          .answer-body {{ color: {c['ink']}; font-size: 1.02rem; line-height: 1.55; }}
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


# --- Inizializzazione dell'agente (cache per provider/modello/chiave) ---
@st.cache_resource
def init_agent(provider: str, model_name: str, api_key: str):
    return DataAgent(
        provider=provider,
        model_name=model_name or None,
        api_key=api_key or None,
    )


agent = init_agent(provider, model_name, api_key)

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

# --- Riga di KPI ---
kpi_cols = st.columns(4)
kpi_cols[0].metric("Righe", f"{len(df):,}")
kpi_cols[1].metric("Colonne", f"{df.shape[1]}")
# Colonne-misura reali (identificatori come Row ID / CAP esclusi), senza duplicati.
# Diamo priorità ai nomi noti se presenti.
measures = measure_columns(df)
priorita = [c for c in ["Sales", "Profit", "Revenue", "Amount", "Total"] if c in measures]
ordinate = priorita + [c for c in measures if c not in priorita]
for slot, col in zip(kpi_cols[2:], ordinate[:2]):
    slot.metric(f"Totale {col}", f"{df[col].sum():,.0f}")

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

st.divider()
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

st.divider()
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
PAROLE_GRAFICO = ["grafico", "mostrami", "visualizza", "plot", "barre", "linee", "andamento"]
PAROLE_LINEA = ["andamento", "linee", "trend", "tempo", "temporale"]

prompt = st.chat_input("Es. 'Qual è il mese con più vendite?' oppure 'Mostrami le vendite per regione'")

if prompt and prompt.strip():
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
