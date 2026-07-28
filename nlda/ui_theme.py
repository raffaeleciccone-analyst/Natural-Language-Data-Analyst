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
          /* Scala tipografica FLUIDA, in un punto solo. Tutte le misure del tema —
             e quelle di Streamlit — sono in rem, cioè multipli di questo valore:
             cambiarlo qui le muove tutte insieme, senza dover mettere un clamp() su
             ogni singola regola (dodici clamp che si devono tenere in accordo a mano
             sono dodici occasioni di sbagliare).
             Il termine `- 0.21vw` fa CRESCERE il testo quando lo schermo è stretto:
             su un portatile 1366-1920px si va a 17.5-18px (+9/+13% rispetto a oggi),
             su un monitor da 2560px si torna ai 16.2px di prima (+1%: invisibile) —
             il tema "strumento di misura" resta compatto dove c'è spazio. I due
             estremi del clamp impediscono sia il gigantismo sotto i 1200px sia il
             rimpicciolimento su un ultrawide. */
          html {{ font-size: clamp(16px, 21.5px - 0.21vw, 18px); }}
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
             .scrollcol identifica le due colonne giuste (KPI e grafici NON scrollano).
             I 470px sono l'intestazione a tutta larghezza (titolo, KPI, anteprima,
             righello) misurata su schermo grande. Da soli erano una trappola: su un
             portatile con viewport alto 621px lasciavano alle colonne 151px, una
             fessura. Il `max(...)` garantisce comunque il 58% dell'altezza dello
             schermo; sotto la soglia del blocco responsive in fondo al file le
             colonne smettono del tutto di scrollare e si impilano. */
          [data-testid="stColumn"]:has(.scrollcol) {{
              max-height: max(58vh, calc(100vh - 470px)); overflow-y: auto; overflow-x: hidden;
              padding-right: 10px; scrollbar-width: thin;
          }}
          [data-testid="stElementContainer"]:has(.scrollcol) {{ display: none !important; }}
          h1, h2, h3, h4 {{ font-family: var(--display); color: {c['ink']}; }}
          /* h2/h3 sono etichette di sezione: maiuscoletto spaziato. h1/h4 restano
             titoli veri, con la crenatura stretta. Le due crenature erano entrambe
             su h2/h3, dove la prima veniva annullata dalla seconda. */
          h1, h4 {{ letter-spacing: -0.01em; }}
          h2, h3 {{ font-size: 1.05rem; text-transform: uppercase; letter-spacing: 0.02em; }}
          .app-subtitle {{ font-family: var(--sans); color: {c['ink2']} !important;
              font-size: 0.86rem; margin-top: -0.4rem; }}
          code, .app-subtitle code {{ font-family: var(--mono); color: {c['deep']}; }}
          [data-testid="stCaptionContainer"] {{ font-family: var(--mono); color: {c['muted']}; }}

          /* ===== RAIL (sidebar scura, non la barra grigia di default) ===== */
          /* Larghezza in REM, non in px: così segue la scala tipografica fluida qui
             sopra. Con i 300px fissi di prima, il testo che cresceva su schermo
             stretto veniva tagliato dal rail che restava fermo ("CSV, XLSX, XL…").
             18.9rem valgono 302px alla scala base — i 300px di sempre sul monitor
             grande, a due pixel — e 340px quando il testo sale a 18px: cresce quanto
             ciò che contiene, e il rapporto fra i due non cambia mai. Il numero è
             misurato sulla riga più lunga del rail ("25MB per file · CSV, XLSX, XLS,
             JSON"): a 18.6rem l'ellissi le mangiava ancora l'ultima parola.
             ⚠️ La larghezza vale SOLO da rail APERTO (`aria-expanded="true"`), ed è
             il punto delicato: Streamlit chiude la barra portandone la larghezza a
             zero, e un `width !important` incondizionato glielo impedisce — la barra
             scompare ma il suo spazio resta prenotato, il contenuto non si ricentra e
             a sinistra rimane una fascia vuota larga quanto il rail. Legata allo
             stato aperto, da chiuso la regola smette di valere e Streamlit richiude
             la larghezza a 0 come fa di suo. Se un domani non esponesse più
             l'attributo, il rail tornerebbe ai suoi 300px di default: si perdono
             pochi pixel sulla riga più lunga, non si rompe il layout. */
          section[data-testid="stSidebar"] {{
              background: {c['rail']}; border-right: 1px solid {c['rail_line']};
          }}
          section[data-testid="stSidebar"][aria-expanded="true"] {{
              width: 18.9rem !important; min-width: 18.9rem !important;
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

          /* ===== Maniglie per chiudere/aprire il sidebar =====
             Streamlit ne espone DUE, entrambe poco visibili: una freccia in alto a
             destra dentro il rail (stSidebarCollapseButton) e, a rail chiuso, un
             pulsante separato fuori dal rail (stExpandSidebarButton). Le portiamo
             tutt'e due alla stessa maniglia circolare centrata sul bordo, così
             l'aspetto non cambia aprendo e chiudendo; `position: fixed` le tiene
             ferme anche mentre il contenuto del sidebar scorre.
             Perché servono entrambe: da CHIUSO il rail è traslato via con una
             `transform` e il `fixed` si ancora a quel frame invece che al viewport
             — la maniglia interna finirebbe fuori schermo. Si riconosce lo stato da
             `aria-expanded="false"`, la si nasconde e si usa l'altra, che stando
             fuori dal rail si ancora pulito. La sua icona è già » (verso l'apertura).
             La FORMA quindi è una sola, scritta una volta: cambiano solo il `left`
             (quello di chiusura assume la larghezza di default del sidebar, ~300px:
             unico numero da ritoccare se Streamlit la cambia) e il nodo che la
             riceve — il contenitore per una, il `button` interno per l'altra. */
          [data-testid="stSidebarCollapseButton"],
          [data-testid="stExpandSidebarButton"] {{
              position: fixed !important; top: 50% !important;
              transform: translateY(-50%) !important; z-index: 1000 !important;
          }}
          /* Segue la larghezza del rail (18.6rem), meno mezza maniglia: resta a
             cavallo del bordo a qualunque scala tipografica. */
          [data-testid="stSidebarCollapseButton"] {{ left: calc(18.9rem - 15px) !important; }}
          [data-testid="stExpandSidebarButton"] {{ left: 6px !important; }}
          [data-testid="stSidebarCollapseButton"],
          [data-testid="stExpandSidebarButton"] button {{
              width: 30px !important; height: 30px !important;
              background: {c['rail2']} !important; border: 1px solid {c['rail_line']} !important;
              border-radius: 50% !important; box-shadow: 0 2px 8px rgba(0,0,0,.4) !important;
              display: flex !important; align-items: center !important; justify-content: center !important;
          }}
          /* Di default Streamlit nasconde la maniglia interna e la mostra solo
             all'hover del rail: la forziamo sempre visibile, è tutto il senso della
             maniglia. Il `button` dentro riempie il cerchio senza ridisegnarlo. */
          [data-testid="stSidebarCollapseButton"] {{ visibility: visible !important; opacity: 1 !important; }}
          [data-testid="stSidebarCollapseButton"] button {{
              width: 100% !important; height: 100% !important; background: transparent !important;
              visibility: visible !important; opacity: 1 !important;
          }}
          /* Da chiuso la maniglia interna sparisce: fa parte del rail traslato via e
             col `position: fixed` riaffiorava fuori posto. */
          section[data-testid="stSidebar"][aria-expanded="false"] [data-testid="stSidebarCollapseButton"] {{
              display: none !important;
          }}
          [data-testid="stSidebarCollapseButton"]:hover {{
              border-color: {c['accent']} !important; background: {c['rail_line']} !important;
          }}
          [data-testid="stExpandSidebarButton"] button:hover {{ border-color: {c['accent']} !important; }}
          [data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"],
          [data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {{
              color: {c['rail_ink']} !important;
          }}
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

          /* ===== SCHERMI PICCOLI (portatili) =====================================
             Il tema nasce su un monitor grande: due colonne affiancate, ciascuna con
             il proprio scroll. Su un portatile quel disegno si ritorce contro, e le
             misure prese sull'app dicono quanto: a 1904x933 la colonna del report era
             alta 463px con 1782px di contenuto dentro — si vedeva il 26% del report
             attraverso una feritoia. A 1350x621 scendeva a 151px.

             La soglia guarda LARGHEZZA **o** ALTEZZA, e l'altezza non è un dettaglio:
             un portatile 1920x1080 è largo abbastanza da passare un test sulla sola
             larghezza, ma il suo viewport è alto ~930px ed è lì che il layout si
             rompe. `max-height: 950px` intercetta proprio quel caso; un 2560x1440
             (viewport ~1300px) resta sopra entrambe le soglie e vede l'app identica
             a prima — questo blocco per lui non esiste. */
          @media (max-width: 1500px), (max-height: 950px) {{
              /* Le due colonne si impilano: report sopra, chat sotto. */
              [data-testid="stHorizontalBlock"]:has(.scrollcol) {{ flex-direction: column; }}
              /* E soprattutto smettono di scrollare per conto loro: a scorrere è la
                 pagina, come si aspetta chiunque apra un sito su un portatile. Senza
                 `min-width` Streamlit terrebbe la larghezza calcolata sul rapporto
                 1.55/1 anche da impilate, lasciando due colonne strette e mezzo
                 schermo vuoto a destra. */
              [data-testid="stColumn"]:has(.scrollcol) {{
                  max-height: none !important; overflow: visible !important;
                  padding-right: 0; width: 100% !important;
                  flex: 1 1 100% !important; min-width: 100% !important;
              }}
              /* Impilate, report e chat si toccherebbero senza stacco. Le separa lo
                 stesso motivo a tacche della classe .scale — la firma del tema, qui
                 riusata come linea di demarcazione invece di un banale bordo grigio. */
              [data-testid="stColumn"]:has(.scrollcol) + [data-testid="stColumn"]:has(.scrollcol) {{
                  margin-top: 18px; padding-top: 22px;
                  background-image: repeating-linear-gradient(90deg, {c['strong']} 0 1px, transparent 1px 8px);
                  background-size: 100% 8px; background-repeat: no-repeat;
              }}
          }}

          /* Solo per la LARGHEZZA: su schermo stretto le file di colonne che NON sono
             le due principali (i 4 KPI, le coppie di grafici, i selettori dentro gli
             expander) vanno a capo invece di stringersi. I 4 KPI diventano 2x2: a
             1350px erano ~250px l'uno, e i valori — mono, 1.9rem, `white-space:nowrap`
             — non ci stavano. Un 1920x1080 tiene i suoi 4 KPI in fila, lì lo spazio
             in larghezza c'è: per questo la regola è separata dal blocco sopra. */
          @media (max-width: 1500px) {{
              [data-testid="stHorizontalBlock"]:not(:has(.scrollcol)) {{ flex-wrap: wrap; }}
              [data-testid="stHorizontalBlock"]:not(:has(.scrollcol)) > [data-testid="stColumn"] {{
                  min-width: 46% !important;
              }}
              /* Margini laterali generosi su 2560px, spreco su 1366px: recuperano
                 ~90px di larghezza utile, che è quasi un quarto di una colonna. */
              .block-container {{ padding-left: 1.6rem !important; padding-right: 1.6rem !important; }}
              /* Le card KPI su due righe raddoppiano l'altezza dell'intestazione, e
                 sotto la piega finirebbe tutto il resto: si stringono in verticale
                 (i 140px di min-height servivano a pareggiare 4 card in fila, cosa
                 che a 2x2 non serve più) recuperando ~50px per riga. */
              .readout {{ min-height: 116px; padding: 14px 12px 12px; }}
              .readout .r-tick {{ margin-top: 10px; }}
              .readout .r-sub {{ margin-top: 7px; }}
          }}

          /* Schermi BASSI: qui il problema non è la larghezza ma i pixel verticali.
             Su un portatile 1366x768 il viewport utile è 621px e l'intestazione a
             tutta larghezza (titolo + sottotitolo + KPI + anteprima + righello) ne
             occupava 614: si apriva l'app e il report cominciava esattamente sotto
             la piega — nessun indizio che sotto ci fosse altro. Titolo e margini
             stretti liberano ~90px, abbastanza perché la prima riga del report si
             veda e inviti a scorrere.
             ⚠️ Il `padding-top` del .block-container NON si tocca: la barra di
             Streamlit (stHeader, alta 3.75rem) è in `position: absolute` e sta SOPRA
             il contenuto — quel padding è ciò che tiene il titolo fuori da lì.
             Ridurlo a 0.7rem faceva finire la prima riga del titolo sotto la barra.
             Essendo in rem, si adegua da sé alla scala tipografica. */
          @media (max-height: 800px) {{
              .stApp h1 {{ font-size: 2.1rem; }}
              .app-subtitle {{ margin-top: -0.2rem; }}
              .readout {{ min-height: 104px; padding: 12px 12px 10px; }}
              .scale {{ margin: 6px 0 2px; }}
          }}
        </style>
        """
