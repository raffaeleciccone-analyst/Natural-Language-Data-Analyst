import html
import os

import pandas as pd
import streamlit as st

from core.loader import (load_dataset, read_any, profile, analyze, measure_columns,
                         best_category, category_columns, monthly_trend,
                         SUPPORTED_EXTENSIONS)
from core.utils import fmt_num, IT_NUM_FORMAT
from core.agent import DataAgent
from core.executor import (execute_pandas_code, summarize_result, apply_theme,
                           to_chart, corr_heatmap)
from core.providers import available_providers, DEFAULT_MODELS, REQUIRES_API_KEY
from core.ui_theme import console_css

# --- Configurazione pagina ---
st.set_page_config(
    page_title="AI Data Analyst Assistant",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY", "groq": "GROQ_API_KEY"}


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


def answer_card(label: str, text: str):
    """Renderizza un testo in un riquadro dedicato (con escaping HTML)."""
    safe = html.escape(text).replace("\n", "<br>")
    st.markdown(
        f"<div class='answer-card'>"
        f"<div class='answer-label'>{html.escape(label)}</div>"
        f"<div class='answer-body'>{safe}</div></div>",
        unsafe_allow_html=True,
    )


def readout(col, label: str, value: str, sub: str = "", tick: str = "#0e7c86", small: bool = False):
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
st.title("AI Data Analyst Assistant")
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


def build_kpis(df, sel_measure, sel_category, unit):
    """
    Costruisce le card KPI adattandosi ai dati: con una MISURA -> totale/media/
    massimo/leader; SENZA misura -> conteggi. Ritorna una lista di tuple
    (label, value, sub, tick, small). Nessun effetto Streamlit (facile da testare).
    """
    def wu(v):  # accosta l'unità di misura, se indicata
        return f"{fmt_num(v)} {unit}".strip() if unit else fmt_num(v)

    kpis = []
    if sel_measure:
        s = df[sel_measure]
        kpis.append((f"Totale {sel_measure}", wu(s.sum()), "", "#0e7c86", False))
        kpis.append((f"Media {sel_measure}", wu(s.mean()), "", "#eda100", False))
        kpis.append((f"Massimo {sel_measure}", wu(s.max()), "", "#2a78d6", False))
        if sel_category:
            leader = df.groupby(sel_category)[sel_measure].sum().sort_values(ascending=False)
            kpis.append((f"Top {sel_category}", str(leader.index[0]), wu(leader.iloc[0]), "#008300", True))
        else:
            kpis.append(("Record", fmt_num(len(df)), "", "#008300", False))
    elif sel_category:  # nessuna misura: KPI a conteggi
        vc = df[sel_category].value_counts()
        kpis.append(("Record", fmt_num(len(df)), "", "#2a78d6", False))
        kpis.append((f"{sel_category} distinte", fmt_num(df[sel_category].nunique()), "", "#eda100", False))
        kpis.append((f"Top {sel_category}", str(vc.index[0]), f"{fmt_num(vc.iloc[0])} record", "#008300", True))
    else:
        kpis.append(("Record", fmt_num(len(df)), "", "#2a78d6", False))
        kpis.append(("Colonne", str(df.shape[1]), "", "#008300", False))
    return kpis


# --- Riga di KPI (adattivi: misure oppure conteggi) ---
kpis = build_kpis(df, sel_measure, sel_category, unit)
kpi_cols = st.columns(len(kpis))
for _col, (_lab, _val, _sub, _tick, _small) in zip(kpi_cols, kpis):
    readout(_col, _lab, _val, sub=_sub, tick=_tick, small=_small)

st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

# --- Anteprima dati ---
with st.expander("Anteprima dei dati (prime 10 righe)"):
    st.dataframe(df.head(10), use_container_width=True)

# --- Report iniziale sui dati ---
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
        corr_fig = None
        if "corr" in insights:
            try:
                corr_fig = corr_heatmap(insights["corr"])
            except Exception:
                corr_fig = None
        st.session_state.top_fig = top_fig
        st.session_state.trend_fig = trend_fig
        st.session_state.corr_fig = corr_fig

insights = st.session_state.get("insights", {})

# Narrativa AI: rigenerata anche al cambio di provider/modello/toggle/unità
overview_sig = (report_sig, spiega_ai, provider, model_name, unit)
if st.session_state.get("overview_sig") != overview_sig:
    st.session_state.overview_sig = overview_sig
    if spiega_ai and insights.get("text"):
        _txt = insights["text"]
        if unit:
            _txt = f"L'unità di misura dei valori è '{unit}'.\n" + _txt
        with st.spinner("L'AI sta preparando la sintesi dei dati..."):
            st.session_state.overview_text = agent.overview(_txt)
    else:
        st.session_state.overview_text = None

st.markdown("<div class='scale'></div>", unsafe_allow_html=True)
st.subheader("Report iniziale sui dati")

# Narrativa AI con i numeri chiave
if st.session_state.get("overview_text"):
    answer_card("Sintesi dei dati", st.session_state.overview_text)

# Insight automatici (numeri calcolati in Pandas, non dedotti dall'AI)
if insights.get("findings"):
    st.markdown("**Insight automatici**")
    st.markdown("\n".join(f"- {f}" for f in insights["findings"]))

# Statistiche numeriche (numeri formattati in modo leggibile)
if "numeric_stats" in insights:
    st.markdown("**Statistiche delle colonne numeriche**")
    stats_disp = insights["numeric_stats"].copy()
    for _c in ["Somma", "Media", "Minimo", "Massimo"]:
        if _c in stats_disp.columns:
            stats_disp[_c] = stats_disp[_c].map(fmt_num)
    st.dataframe(stats_disp, use_container_width=True, hide_index=True)

def render_linked_charts(df, insights, top_fig, trend_fig):
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
            event = st.plotly_chart(apply_theme(top_fig), use_container_width=True,
                                    on_select="rerun", key="report_top")
            try:
                pts = event.selection.points
                if pts:
                    px_ = pts[0].get("x")
                    selected_cat = px_ if isinstance(px_, str) else pts[0].get("y")
            except Exception:
                selected_cat = None
        idx += 1

    if trend_fig is not None and "trend" in insights:
        dcol, num, _ = insights["trend"]
        cat = insights["top"][0] if "top" in insights else None
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
                                use_container_width=True, key="report_trend_filtered")
            else:
                st.markdown(f"**Andamento di {num} nel tempo**")
                st.plotly_chart(apply_theme(trend_fig), use_container_width=True, key="report_trend")


render_linked_charts(df, insights, st.session_state.get("top_fig"),
                     st.session_state.get("trend_fig"))

# --- Correlazioni tra le misure (calcolate in Pandas) ---
corr_fig = st.session_state.get("corr_fig")
if corr_fig is not None:
    st.markdown("**Correlazioni tra le misure**")
    pairs = insights.get("corr_pairs") or []
    if pairs:
        a, b, r = pairs[0]
        verso = "positiva" if r > 0 else "negativa"
        st.caption(f"Coppia più correlata: {a} e {b} (r = {fmt_num(r)}, {verso}). "
                   "La correlazione indica associazione, non causa.")
    else:
        st.caption("Nessuna coppia con correlazione forte (|r| ≥ 0,6).")
    st.plotly_chart(apply_theme(corr_fig), use_container_width=True, key="report_corr")

with st.expander("Struttura delle colonne (tipi, mancanti, valori)"):
    st.dataframe(st.session_state.get("profile"), use_container_width=True, hide_index=True)

st.markdown("<div class='scale'></div>", unsafe_allow_html=True)
st.subheader("Fai una domanda ai tuoi dati")


# --- Rendering di un risultato (kp = chiave univoca del turno, evita ID duplicati) ---
def render_value(value, kp: str = "r"):
    """Mostra il dato del risultato (tabella / numero / testo), con numeri leggibili."""
    if value is None:
        return
    if isinstance(value, pd.DataFrame):
        num_cols = list(value.select_dtypes("number").columns)
        styled = (value.style.format(subset=num_cols, **IT_NUM_FORMAT)
                  if num_cols else value)
        st.dataframe(styled, use_container_width=True, hide_index=True, key=f"{kp}_df")
    elif isinstance(value, pd.Series):
        st.dataframe(value.rename("valore").to_frame(), use_container_width=True, key=f"{kp}_ser")
    elif isinstance(value, (int, float)):
        st.metric("Risultato", fmt_num(value))
    elif isinstance(value, str) and value != "Codice eseguito correttamente.":
        st.markdown(f"**Risultato:** {value}")


def render_result(code: str, result, explanation: str | None = None, kp: str = "r"):
    # 1. Risposta testuale (in un riquadro dedicato)
    if explanation:
        answer_card("Risposta", explanation)

    # 2. Risultato visuale: grafico E/O dati
    if isinstance(result, str):  # stringa d'errore
        st.error(result)
    elif isinstance(result, dict):
        if result.get("fig") is not None:
            st.plotly_chart(apply_theme(result["fig"]), use_container_width=True, key=f"{kp}_fig")
        render_value(result.get("value"), kp)
    else:
        render_value(result, kp)

    # 3. Codice generato (in fondo, collassato)
    with st.expander("Codice Pandas generato"):
        st.code(code, language="python")


if "messages" not in st.session_state:
    st.session_state.messages = []


def process_question(prompt: str):
    """Genera il codice, lo esegue (con retry), produce la spiegazione e salva il turno."""
    with st.spinner("Analisi in corso..."):
        # L'agente decide da sé se serve un grafico e avvolge i dati (unica fonte).
        codice = agent.ask_code(prompt, df)
        risultato = execute_pandas_code(codice, df)

        tentativo = 0
        while (isinstance(risultato, str) and risultato.startswith("Errore") and tentativo < 3):
            tentativo += 1
            codice = agent.fix_code(prompt, df, codice, risultato)
            risultato = execute_pandas_code(codice, df)

        spiegazione = None
        if spiega_ai and isinstance(risultato, dict):
            _summ = risultato.get("summary") or summarize_result(risultato)
            if unit:
                _summ = f"Unità di misura: '{unit}'.\n" + _summ
            spiegazione = agent.explain(prompt, _summ)

    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.messages.append(
        {"role": "assistant", "code": codice, "result": risultato, "explanation": spiegazione}
    )


# --- Box domanda (inline, integrato nel flusso) ---
with st.form("ask_form", clear_on_submit=True):
    c_in, c_btn = st.columns([8, 1])
    user_q = c_in.text_input(
        "domanda", label_visibility="collapsed",
        placeholder="Es. 'Qual è il mese con più vendite?' oppure 'Mostrami le vendite per regione'",
    )
    submitted = c_btn.form_submit_button("Invia", use_container_width=True)

if submitted and user_q and user_q.strip():
    if DEMO_MODE and st.session_state.get("_demo_q", 0) >= DEMO_MAX_QUESTIONS:
        st.warning(f"Hai raggiunto il limite della demo ({DEMO_MAX_QUESTIONS} domande). "
                   "Clona il repo da GitHub per uso illimitato. Grazie per aver provato.")
    else:
        if DEMO_MODE:
            st.session_state["_demo_q"] = st.session_state.get("_demo_q", 0) + 1
        process_question(user_q.strip())

# --- Storico conversazione (turno più recente in alto) ---
_msgs = st.session_state.messages
_turns = []
_i = 0
while _i < len(_msgs):
    u = _msgs[_i]
    a = _msgs[_i + 1] if _i + 1 < len(_msgs) and _msgs[_i + 1]["role"] == "assistant" else None
    _turns.append((u, a))
    _i += 2 if a else 1

for _oi, (u, a) in reversed(list(enumerate(_turns))):
    with st.chat_message("user"):
        st.write(u["content"])
    if a is not None:
        with st.chat_message("assistant"):
            render_result(a["code"], a["result"], a.get("explanation"), kp=f"h{_oi}")
