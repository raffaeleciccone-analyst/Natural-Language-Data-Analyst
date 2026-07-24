"""
Stile dell'interfaccia — tema "strumento di misura": rail (sidebar) scuro,
workspace freddo, un solo accento teal usato con parsimonia. Tenuto fuori da
main.py per non mescolare presentazione e logica: main.py inietta `console_css()`.

⚠️ SCELTA CONSAPEVOLE — i selettori `[data-testid="st..."]` (stSidebar, stForm,
stMetricValue, stChatMessage, ...) NON sono un'API pubblica di Streamlit: sono
dettagli interni del suo DOM. A un aggiornamento di Streamlit possono cambiare o
sparire, e allora la regola CSS smette di applicarsi in silenzio — il layout non
si rompe, si limita a perdere lo stile mirato. Si accetta il rischio perché non
esiste un modo ufficiale per stilare quei componenti; se un giorno il tema si
"slega", il primo posto da controllare è se questi testid sono cambiati. Le classi
custom (`.readout`, `.answer-card`, `.scale`) sono definite da noi e stabili.
"""

# Palette dell'UI, fonte unica: la leggono il CSS qui sotto, i grafici
# (`nlda.charts._THEME`) e le tacche dei KPI (`nlda.ui_components`).
# ⚠️ Per questo il modulo NON deve importare nulla: `nlda.charts` viene caricato
# anche dal worker della sandbox, e una dipendenza aggiunta qui — Streamlit su
# tutte — entrerebbe nel costo di avvio di ogni esecuzione di codice.
# Chiavi "storiche" (page, surface, accent...) + le tinte del rail.
PALETTE = dict(
    page="#f2f4f6", surface="#ffffff", surface2="#f7f9fb",
    border="#e4e8ec", strong="#d5dbe1", ink="#1b1f24", ink2="#586471",
    muted="#8b97a3", accent="#0d8a7d", deep="#0a6b61", tint="#e3f0ee",
    bar="#c8d0d8",
    rail="#191d23", rail2="#20262e", rail_ink="#d6dde4",
    rail_soft="#8a95a1", rail_line="#2b323b",
)


def console_css() -> str:
    """Restituisce il blocco <style> completo dell'interfaccia."""
    c = PALETTE
    return f"""
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
          .block-container {{ padding-top: 1.6rem; max-width: 1700px; }}
          /* Colonne affiancate allineate in ALTO: la chat, più corta del report, non
             viene stirata alla sua altezza — così sotto la chat non resta mezza
             colonna vuota. Vale anche per KPI e coppie di grafici (già di pari altezza). */
          [data-testid="stHorizontalBlock"] {{ align-items: flex-start; }}
          /* Scroll indipendente delle due colonne SENZA st.container(height=...):
             quello resettava lo scroll a ogni rerun (un expander sembrava non aprirsi).
             Qui l'overflow sta sul div STABILE della colonna, che il browser conserva
             tra i rerun: cliccare un expander lo apre e lo scroll resta dov'è. Il marker
             .scrollcol identifica le due colonne giuste (KPI e grafici NON scrollano). */
          [data-testid="stColumn"]:has(.scrollcol) {{
              max-height: calc(100vh - 470px); overflow-y: auto; overflow-x: hidden;
              padding-right: 10px; scrollbar-width: thin;
          }}
          [data-testid="stElementContainer"]:has(.scrollcol) {{ display: none !important; }}
          h1, h2, h3, h4 {{ font-family: var(--display); color: {c['ink']}; letter-spacing: -0.01em; }}
          h2, h3 {{ font-size: 1.05rem; text-transform: uppercase; letter-spacing: 0.02em; }}
          .app-subtitle {{ font-family: var(--sans); color: {c['ink2']} !important;
              font-size: 0.86rem; margin-top: -0.4rem; }}
          code, .app-subtitle code {{ font-family: var(--mono); color: {c['deep']}; }}
          [data-testid="stCaptionContainer"] {{ font-family: var(--mono); color: {c['muted']}; }}

          /* ===== RAIL (sidebar scura, non la barra grigia di default) ===== */
          section[data-testid="stSidebar"] {{
              background: {c['rail']}; border-right: 1px solid {c['rail_line']};
          }}
          section[data-testid="stSidebar"] .stMarkdown,
          section[data-testid="stSidebar"] p,
          section[data-testid="stSidebar"] label,
          section[data-testid="stSidebar"] li,
          section[data-testid="stSidebar"] span {{ color: {c['rail_ink']}; }}
          section[data-testid="stSidebar"] h1,
          section[data-testid="stSidebar"] h2,
          section[data-testid="stSidebar"] h3 {{ color: #ffffff; }}
          /* Sidebar COMPATTO: i 5 divisori (margine 32px sopra+sotto) e il gap da 16px
             fra ogni widget facevano sforare il contenuto → barra a scorrimento. Stretti
             così, tutto entra e la sidebar resta ferma. */
          section[data-testid="stSidebar"] hr {{ margin: 7px 0 !important; }}
          section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0.5rem !important; }}
          section[data-testid="stSidebar"] h2,
          section[data-testid="stSidebar"] h3 {{ margin: 0.15rem 0 !important; }}
          /* Banda scura in CIMA: l'header del sidebar (~76px) e lo spazio-logo
             esistevano per la freccia di chiusura, ora maniglia fissa sul bordo.
             Azzerati, il contenuto parte dall'alto invece che 76px più giù. */
          section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {{
              height: 0 !important; min-height: 0 !important; margin: 0 !important;
          }}
          section[data-testid="stSidebar"] [data-testid="stLogoSpacer"] {{ display: none !important; }}
          section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {{ padding-top: 0.6rem !important; }}
          /* Menu a tendina del selectbox più basso: l'ultimo in fondo (Filtro) apriva
             un elenco da 300px che sbordava sotto lo schermo. Con max-height ridotta il
             menu resta nello schermo e le voci in più scorrono al suo interno. */
          [role="listbox"] {{ max-height: 240px !important; }}
          section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{ color: {c['rail_soft']}; }}
          /* Campi scuri dentro il rail. Due trappole trovate testando la demo:
             1) il selectbox di Streamlit non espone più `data-baseweb="select"` (è
                passato a React-Aria): il controllo si aggancia per testid + la famiglia
                di classi emotion `e1fp86qc0`, altrimenti resta bianco sul rail scuro;
             2) il pulsante nativo dell'uploader (`stBaseButton-secondary`) nasce BIANCO,
                e col testo reso chiaro dal tema del rail il suo "Upload" spariva
                (chiaro su bianco). Serve scurirne lo sfondo. */
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input,
          section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{
              background: {c['rail2']}; border-color: {c['rail_line']}; color: {c['rail_ink']};
          }}
          /* Il testo-esempio (placeholder, es. "es. €, kg, %") ha un grigio di default
             troppo scuro sul rail: lo si porta a un grigio leggibile. */
          section[data-testid="stSidebar"] [data-testid="stTextInput"] input::placeholder {{
              color: {c['rail_soft']} !important; opacity: 1 !important;
          }}
          section[data-testid="stSidebar"] [data-testid="stSelectbox"] [class*="e1fp86qc0"] {{
              background: {c['rail2']} !important; border-color: {c['rail_line']} !important;
          }}
          section[data-testid="stSidebar"] [data-testid="stSelectbox"] [class*="e1fp86qc0"],
          section[data-testid="stSidebar"] [data-testid="stSelectbox"] [class*="e1fp86qc0"] * {{
              color: {c['rail_ink']} !important;
          }}
          section[data-testid="stSidebar"] [data-testid="stSelectbox"] svg {{ fill: {c['rail_soft']} !important; }}
          section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {{
              background: {c['rail2']} !important; color: {c['rail_ink']} !important;
              border: 1px solid {c['rail_line']} !important;
          }}
          section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover {{
              border-color: {c['accent']} !important; color: #ffffff !important;
          }}

          /* ===== Maniglia per chiudere/aprire il sidebar =====
             Di default è una piccola freccia in alto a destra, poco visibile. La
             portiamo a maniglia circolare centrata verticalmente sul BORDO del rail:
             sempre in vista e comoda da cliccare. `position: fixed` la tiene ferma
             anche quando il contenuto del sidebar scorre. Il `left` assume la
             larghezza di default del sidebar (~300px); se Streamlit la cambia, è
             l'unico numero da ritoccare. */
          [data-testid="stSidebarCollapseButton"] {{
              position: fixed !important; top: 50% !important; left: 286px !important;
              transform: translateY(-50%) !important; z-index: 1000 !important;
              width: 30px !important; height: 30px !important;
              background: {c['rail2']} !important; border: 1px solid {c['rail_line']} !important;
              border-radius: 50% !important; box-shadow: 0 2px 8px rgba(0,0,0,.4) !important;
              display: flex !important; align-items: center !important; justify-content: center !important;
              /* Di default Streamlit la nasconde e la mostra solo all'hover del rail:
                 la forziamo sempre visibile, è tutto il senso della maniglia. */
              visibility: visible !important; opacity: 1 !important;
          }}
          [data-testid="stSidebarCollapseButton"]:hover {{
              border-color: {c['accent']} !important; background: {c['rail_line']} !important;
          }}
          [data-testid="stSidebarCollapseButton"] button {{
              width: 100% !important; height: 100% !important; background: transparent !important;
              visibility: visible !important; opacity: 1 !important;
          }}
          [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"] {{
              color: {c['rail_ink']} !important;
          }}

          /* Sidebar CHIUSO: è lo STESSO pulsante (Streamlit non ne espone uno
             separato), ma il rail viene traslato via con una `transform`, e
             `position: fixed` si ancora a quel frame traslato invece che al viewport —
             la maniglia finirebbe fuori schermo a sinistra. Si riconosce lo stato da
             `aria-expanded="false"` e si compensa lo scostamento (~299px, la larghezza
             del rail) così resta una maniglia comoda sul bordo sinistro. */
          /* Da CHIUSO il pulsante interno del rail (stSidebarCollapseButton) va
             nascosto: fa parte del rail traslato via, e col `position: fixed` finiva
             per riaffiorare fuori posto. Il VERO controllo di riapertura è un elemento
             separato, stExpandSidebarButton (fuori dal rail, quindi il fixed si ancora
             pulito al viewport): lo porto a maniglia centrata sul bordo sinistro,
             simmetrica a quella di chiusura. La sua icona è già » (verso l'apertura). */
          section[data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarCollapseButton"] {{
              display: none !important;
          }}
          [data-testid="stExpandSidebarButton"] {{
              position: fixed !important; top: 50% !important; left: 6px !important;
              transform: translateY(-50%) !important; z-index: 1000 !important;
          }}
          [data-testid="stExpandSidebarButton"] button {{
              width: 30px !important; height: 30px !important;
              background: {c['rail2']} !important; border: 1px solid {c['rail_line']} !important;
              border-radius: 50% !important; box-shadow: 0 2px 8px rgba(0,0,0,.4) !important;
              display: flex !important; align-items: center !important; justify-content: center !important;
          }}
          [data-testid="stExpandSidebarButton"] button:hover {{ border-color: {c['accent']} !important; }}
          [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {{ color: {c['rail_ink']} !important; }}
          section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {{
              font-family: var(--mono); font-size: 0.66rem; letter-spacing: 0.12em;
              text-transform: uppercase; color: {c['rail_soft']}; }}

          /* ===== KPI readout ===== */
          .readout {{
              background: {c['surface']}; border: 1px solid {c['border']};
              border-radius: 12px; padding: 20px 16px 18px; min-height: 140px;
              box-shadow: 0 1px 2px rgba(20,30,40,0.04); text-align: center;
          }}
          .readout .r-k {{ font-family: var(--mono); font-size: 0.66rem; letter-spacing: 0.1em;
              text-transform: uppercase; color: {c['ink2']}; }}
          .readout .r-v {{ font-family: var(--mono); font-weight: 600; font-size: 1.9rem;
              letter-spacing: -0.03em; margin-top: 10px; line-height: 1.1;
              font-variant-numeric: tabular-nums; color: {c['ink']}; white-space: nowrap; }}
          .readout .r-v.sm {{ font-family: var(--display); font-size: 1.55rem; letter-spacing: 0;
              line-height: 1.15; margin-top: 12px;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
          .readout .r-tick {{ height: 5px; margin: 14px auto 0; border-radius: 3px;
              background: var(--bar, {c['accent']}); width: 48px; }}
          .readout .r-sub {{ font-family: var(--mono); font-size: 0.72rem; color: {c['muted']}; margin-top: 10px; }}

          /* Motivo "scala di misura" — la firma, sottile */
          .scale {{ height: 8px; margin: 10px 0 2px;
              background-image: repeating-linear-gradient(90deg, {c['strong']} 0 1px, transparent 1px 8px); }}

          /* ===== Riquadro risposta / sintesi (niente più callout con bordo colorato) ===== */
          .answer-card {{
              background: {c['surface2']}; border: 1px solid {c['border']};
              border-radius: 12px; padding: 14px 16px; margin: 4px 0 14px 0;
          }}
          .answer-label {{
              font-family: var(--mono); color: {c['deep']}; font-weight: 600;
              font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.12em;
              margin-bottom: 8px;
          }}
          .answer-body {{ color: {c['ink']}; font-size: 0.98rem; line-height: 1.6; }}
          .answer-body b {{ color: {c['deep']}; font-weight: 600; }}

          /* ===== Componenti nativi ===== */
          [data-testid="stDataFrame"] {{ border-radius: 10px; }}
          div[data-testid="stMetricValue"] {{ font-family: var(--mono); color: {c['accent']}; font-weight: 600; }}
          div[data-testid="stMetricLabel"] {{ font-family: var(--mono); color: {c['ink2']}; }}
          div[data-testid="stChatMessage"] {{
              background: {c['surface']}; border: 1px solid {c['border']};
              border-radius: 12px; padding: 4px 12px;
          }}
          .stExpander {{ border-radius: 10px; border: 1px solid {c['border']}; }}
          .stExpander summary {{ font-family: var(--mono); font-size: 0.78rem;
              text-transform: uppercase; letter-spacing: 0.04em; color: {c['ink2']}; }}

          /* Input della chat + pulsanti in accento teal */
          [data-testid="stTextInput"] input {{
              border-radius: 10px; border: 1px solid {c['strong']};
              background: {c['surface']}; color: {c['ink']};
          }}
          [data-testid="stTextInput"] input:focus {{ border-color: {c['accent']}; box-shadow: 0 0 0 2px {c['tint']}; }}
          [data-testid="stFormSubmitButton"] button,
          [data-testid="stButton"] button[kind="primary"] {{
              background: {c['accent']}; color: #ffffff; border: 0;
              border-radius: 10px; font-weight: 600; font-family: var(--display);
          }}
          [data-testid="stFormSubmitButton"] button:hover,
          [data-testid="stButton"] button[kind="primary"]:hover {{ background: {c['deep']}; }}
          [data-testid="stDownloadButton"] button {{
              border-radius: 9px; border: 1px solid {c['border']};
              color: {c['deep']}; font-family: var(--mono); font-size: 0.8rem;
          }}
        </style>
        """
