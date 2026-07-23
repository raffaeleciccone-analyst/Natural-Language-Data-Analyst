"""
Stile dell'interfaccia "Console" (tema chiaro). Tenuto fuori da main.py per non
mescolare presentazione e logica: main.py inietta soltanto `console_css()`.

⚠️ SCELTA CONSAPEVOLE — i selettori `[data-testid="st..."]` (stSidebar, stForm,
stMetricValue, stChatMessage, ...) NON sono un'API pubblica di Streamlit: sono
dettagli interni del suo DOM. A un aggiornamento di Streamlit possono cambiare o
sparire, e allora la regola CSS smette di applicarsi in silenzio — il layout non
si rompe, si limita a perdere lo stile mirato. Si accetta il rischio perché non
esiste un modo ufficiale per stilare quei componenti; se un giorno il tema si
"slega", il primo posto da controllare è se questi testid sono cambiati. La classe
`.block-container` e le classi custom (`.readout`, `.answer-card`, `.scale`) non
hanno questo problema: sono stabili o definite da noi.
"""

# Palette dell'UI (tema chiaro)
PALETTE = dict(
    page="#eff1ee", surface="#ffffff", surface2="#f7f8f6",
    border="#e2e6e1", strong="#d3d8d1", ink="#16191c", ink2="#59626b",
    muted="#8a929a", accent="#0e7c86", deep="#0a5960", tint="#e2f0f0",
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
              height: 120px; overflow: hidden;
          }}
          .readout .r-k {{ font-family: var(--mono); font-size: 0.66rem; letter-spacing: 0.1em;
              text-transform: uppercase; color: {c['ink2']}; }}
          .readout .r-v {{ font-family: var(--mono); font-weight: 600; font-size: 1.85rem;
              letter-spacing: -0.02em; margin-top: 7px; line-height: 1;
              font-variant-numeric: tabular-nums; color: {c['ink']}; }}
          .readout .r-v.sm {{ font-size: 1.1rem; line-height: 1.2; margin-top: 12px;
              white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
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
          /* Box domanda: fissato in alto (sempre raggiungibile senza scrollare) */
          [data-testid="stForm"] {{
              position: sticky; top: 0; z-index: 60;
              background: {c['page']}; padding: 8px 0 6px;
          }}
          /* Box domanda inline: input pulito + pulsante in accento */
          [data-testid="stTextInput"] input {{
              border-radius: 10px; border: 1px solid {c['strong']};
              background: {c['surface']}; color: {c['ink']};
          }}
          [data-testid="stTextInput"] input:focus {{ border-color: {c['accent']}; }}
          [data-testid="stFormSubmitButton"] button {{
              background: {c['accent']}; color: #ffffff; border: 0;
              border-radius: 10px; font-weight: 600; font-family: var(--display);
          }}
          [data-testid="stFormSubmitButton"] button:hover {{ background: {c['deep']}; }}
        </style>
        """
