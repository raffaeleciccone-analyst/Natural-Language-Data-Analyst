# Documentazione Tecnica Completa
# Natural Language Data Analyst

> **Versione documento:** 3.0 — 5 agosto 2026  
> **Progetto documentato:** `natural-language-data-analyst` v2.0.0 (251 commit)  
> **Scopo:** rendere un nuovo sviluppatore capace non solo di *usare* il codice, ma di
> *comprenderlo, modificarlo e difenderne ogni scelta* in un colloquio tecnico.  
> **Livello del lettore atteso:** Python intermedio, prima esposizione a Streamlit/LLM.

> **Cosa è cambiato dalla 2.0 (30 luglio).** Il progetto ha preso una **seconda
> interfaccia**: un'API HTTP (`nlda/api/`, §7.31–7.34) e un frontend React
> (`frontend/`, §7.35) montati sopra lo stesso backend, senza spostare una riga di
> logica — ed è il collaudo vero della stratificazione descritta in §4. Sono
> documentati anche i moduli condivisi nati con essa (`views`, `kpis`,
> `suggestions`, `demo_data`, `project_qa`).
> **Il tema di questa revisione, però, è un altro:** cosa fa lo strumento quando
> *non può* rispondere. Il modello ora dichiara su quale colonna sta rispondendo e
> la dichiarazione viene verificata (§7.14); un'unione che duplica le righe lo dice
> (§7.28); un file storto viene rifiutato con la ragione invece di diventare una
> tabella plausibile (§7.12); una libreria assente non innesca tre tentativi
> destinati a fallire (§7.5). Sono correzioni nate **riproducendo** i difetti, e
> in cinque casi su nove il comportamento reale si è rivelato peggiore di come era
> stato descritto.
> I test sono passati da 27 file/476 test a **34 file, 679 test**, più 12
> sull'interfaccia React.

> **Nota di lettura.** Le sezioni 1–12 descrivono il progetto come è oggi. Dove una
> scelta è stata *cambiata* nel tempo, il documento dice anche com'era prima e
> perché non andava: è la parte che serve davvero in un colloquio, perché una
> decisione si difende solo conoscendo l'alternativa che è stata scartata.

---

## Indice

1. [Introduzione e visione d'insieme](#1-introduzione-e-visione-dinsieme)
2. [Il principio cardine: "l'LLM racconta, Pandas calcola"](#2-il-principio-cardine-lllm-racconta-pandas-calcola)
3. [Stack tecnologico e dipendenze](#3-stack-tecnologico-e-dipendenze)
4. [Architettura generale](#4-architettura-generale)
5. [Il flusso dell'applicazione, passo per passo](#5-il-flusso-dellapplicazione-passo-per-passo)
6. [Struttura delle cartelle](#6-struttura-delle-cartelle)
7. [Analisi file per file, funzione per funzione](#7-analisi-file-per-file-funzione-per-funzione)
8. [La sandbox di sicurezza in profondità](#8-la-sandbox-di-sicurezza-in-profondità)
9. [I test](#9-i-test)
10. [Configurazione, CI, container e deploy](#10-configurazione-ci-container-e-deploy)
11. [Design patterns e principi applicati](#11-design-patterns-e-principi-applicati)
12. [Limiti noti e possibili evoluzioni](#12-limiti-noti-e-possibili-evoluzioni)

---

## 1. Introduzione e visione d'insieme

**Natural Language Data Analyst** (NLDA) è un'applicazione web che permette di
interrogare un dataset **in linguaggio naturale**. L'utente carica un file (CSV,
Excel o JSON), pone una domanda come *"Qual è il mese con più vendite?"*, e
l'applicazione:

1. traduce la domanda in **codice Pandas** tramite un modello LLM;
2. **esegue** quel codice in una sandbox di sicurezza;
3. restituisce il **risultato numerico**, un eventuale **grafico Plotly interattivo**
   e una **risposta testuale** che interpreta i numeri.

Oltre a rispondere alle domande, al caricamento del dataset l'app produce un
**report automatico**: KPI, statistiche, classifiche, andamento temporale,
correlazioni, insight e un **report esecutivo** scaricabile. La conversazione è
esportabile in Markdown, e ogni risposta porta con sé due appigli di fiducia: le
colonne che il codice ha davvero toccato e gli eventuali avvisi di plausibilità.

### A cosa serve questa documentazione

Il progetto è piccolo per numero di righe (~5.300 nel pacchetto) ma denso di
**scelte progettuali non banali**, quasi tutte legate a tre tensioni ricorrenti:

- **Affidabilità contro un LLM inaffidabile.** Un modello che genera codice può
  sbagliare, allucinare colonne, o essere spinto da un file ostile a produrre
  codice pericoloso. Gran parte dell'architettura esiste per *contenere* questa
  inaffidabilità.
- **Sicurezza dell'esecuzione di codice generato.** Eseguire codice scritto da un
  LLM su dati caricati dall'utente è, di fatto, *arbitrary code execution*. La
  sandbox (allowlist AST + sottoprocesso isolato) è il cuore difensivo.
- **Fiducia dell'utente in un numero che non ha calcolato lui.** Un risultato
  "eseguibile ma sbagliato" è il guasto più insidioso: Pandas non solleva niente,
  ma il numero risponde a un'altra domanda. Da qui i controlli di `checks.py`.

Chi capisce queste tre tensioni capisce il 90% delle scelte del progetto.

---

## 2. Il principio cardine: "l'LLM racconta, Pandas calcola"

Se dovessi memorizzare **una sola frase** su questo progetto, è questa (è anche la
`description` in `pyproject.toml`):

> **I numeri li calcola Pandas. L'LLM si limita a raccontarli.**

### Perché è la decisione più importante

Un LLM è un pessimo calcolatore: se gli chiedi *"quanto fanno queste vendite in
totale"* può inventare un numero plausibile ma sbagliato (allucinazione
numerica). Al contrario, è un ottimo *traduttore* (domanda → codice) e un ottimo
*narratore* (numeri → prosa).

Il progetto sfrutta questa asimmetria dividendo nettamente i ruoli:

```
            ┌─────────────────────────────────────────────────────┐
            │                       LLM                            │
            │  • TRADUCE la domanda in codice Pandas               │
            │  • RACCONTA in italiano i numeri già calcolati       │
            │  ✗ NON calcola mai un numero da mostrare all'utente  │
            └─────────────────────────────────────────────────────┘
                                    │
                                    ▼
            ┌─────────────────────────────────────────────────────┐
            │                  Pandas / NumPy                      │
            │  • CALCOLA ogni numero (somme, medie, %, correlaz.)  │
            │  • È la UNICA fonte di verità numerica               │
            └─────────────────────────────────────────────────────┘
```

**Conseguenze concrete nel codice** (le ritroverai ovunque):

- Gli insight automatici (`_findings` in `loader.py`) sono calcolati in Pandas e
  **passati** all'LLM come testo già pronto; il prompt del report esecutivo
  (`prompts/executive_report.md`) ripete *"Usa SOLO i numeri presenti nell'input;
  non calcolarne di nuovi e non inventarne"*.
- La spiegazione (`agent.explain`) riceve un riepilogo — cioè numeri già
  calcolati dal runner — e deve solo interpretarlo.
- Il confronto tra periodi non è affidato al modello: `periods.compare_periods` è
  un motore deterministico, testato, che la sandbox **espone** al codice generato
  e che la UI chiama direttamente. Il modello non reinventa la variazione
  percentuale a ogni domanda.
- Persino i KPI in cima alla pagina (`build_kpis`) sono Pandas puro, mai LLM.
- `checks.claimed_missing_columns` verifica che la *spiegazione* non nomini
  colonne o entità assenti dal risultato: il narratore non può aggiungere fatti.

**Analogia:** l'LLM è un *giornalista*; Pandas è il *contabile*. Il giornalista
scrive l'articolo, ma i numeri glieli passa il contabile. Nessun giornalista
serio inventa il bilancio.

---

## 3. Stack tecnologico e dipendenze

### Dipendenze dell'app (pinnate per riproducibilità)

| Pacchetto | Versione | Ruolo |
|-----------|----------|-------|
| `streamlit` | 1.60.0 | framework UI web (l'intera interfaccia) |
| `pandas` | 3.0.5 | motore di calcolo dati, unica fonte di verità numerica |
| `numpy` | 2.5.1 | dipendenza di pandas; pinnata perché determina gli stub di tipo in CI |
| `plotly` | 6.9.0 | grafici interattivi |
| `openpyxl` | 3.1.5 | lettura file Excel `.xlsx` |

### Due manifest, due scopi (dettaglio che vale una domanda)

La fonte di verità è **`pyproject.toml`** (PEP 621): contiene le dipendenze
dell'app, gli extra per i provider e l'extra `dev`. In locale si installa con
`pip install -e ".[all,dev]"`.

**`requirements.txt` esiste solo per il deploy**: Streamlit Community Cloud
installa esclusivamente da quel file e non sa leggere `pyproject.toml`. Contiene
il minimo per la demo pubblica — l'app più il **solo SDK del provider che il
deploy usa davvero** (Groq, che parla l'API OpenAI). Non è una duplicazione per
distrazione: è un vincolo della piattaforma, documentato in testa al file.

`requirements-dev.txt` **non esiste più**: gli strumenti di sviluppo sono l'extra
`dev` di `pyproject.toml`.

### Provider LLM (opzionali, floor + tetto di major)

| Extra | Vincolo | Provider |
|-------|---------|----------|
| `ollama` | `==0.6.2` | modelli locali (default, nessuna API key) |
| `anthropic` | `>=0.40,<1.0` | Claude |
| `openai` | `>=1.40,<3.0` | GPT **e** Groq (API compatibile) |
| `gemini` | `>=0.3,<3.0` | Gemini |
| `all` | — | tutti e quattro |

**Perché l'app è pinnata con `==` e i provider con range?** L'app è il set testato
in CI: fissarlo garantisce che il test di oggi valga anche domani. I provider sono
opzionali e non entrano nel percorso testato in CI, quindi si tollera un range
(floor per avere le feature necessarie, tetto di major per non farsi rompere da
una release breaking).

### Strumenti di sviluppo (extra `dev`)

- `pytest` + `pytest-cov` — test con copertura **misurata e applicata in CI**
- `hypothesis` — property/fuzz test sul validatore della sandbox
- `ruff` — lint
- `mypy` — type-check statico

> **Nota di dettaglio (spesso chiesta):** `pandas-stubs` **non** è incluso di
> proposito. Le sue release seguono pandas 2.x, ma qui si usa pandas 3.0: gli
> stub disallineati generavano decine di falsi positivi in mypy. Meglio nessuno
> stub per pandas (type-check comunque attivo sul resto).

L'extra `tools` (`pillow`, `scipy`) serve solo agli script accessori in
`scripts/`, non all'app.

### Python 3.12 come minimo

`requires-python = ">=3.12"` e `python_version = "3.12"` in mypy: **ora
coincidono**. Nella 1.0 di questo documento divergevano (runtime da 3.10, mypy a
3.12) perché gli stub di numpy 2.5 usano la sintassi `type X = ...` (PEP 695) che
mypy sa parsare solo da 3.12. Allineare il minimo di runtime ha eliminato la
divergenza: un solo numero da difendere invece di due. La CI verifica **3.12, 3.13
e 3.14**.

---

## 4. Architettura generale

### 4.1 Vista a strati

L'app è organizzata a **strati** con una regola di dipendenza chiara: gli strati
alti dipendono dai bassi, mai il contrario.

```
┌──────────────────────────────────────────────────────────────────────┐
│  PRESENTAZIONE — DUE interfacce, lo stesso backend                     │
│                                                                        │
│  A) Streamlit (in-process)                                             │
│  main.py              il FLUSSO della pagina ("cosa mostrare quando") │
│  nlda/ui/pages.py     le sezioni: render_sidebar, render_kpis, chat   │
│  nlda/ui/session.py   plumbing: secret, quota demo, cache, dati       │
│  nlda/ui_components.py  pezzi riusabili ("come si mostra")            │
│  nlda/ui_theme.py     CSS del tema "strumento di misura"              │
│                                                                        │
│  B) React (via HTTP)                                                   │
│  frontend/            SPA TypeScript: chat in streaming, report, filtro│
│  nlda/api/app.py      le rotte: traduzione da e verso JSON             │
│  nlda/api/models.py   le forme dei dati → schema OpenAPI → tipi TS     │
│  nlda/api/store.py    dove vive un dataset fra due richieste HTTP      │
│  nlda/api/quota.py    il tetto di spesa della demo pubblica            │
│  nlda/api/streaming.py  un turno trasmesso a pezzi (SSE)               │
└──────────────────────────────────────────────────────────────────────┘
                                   │ usa
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  APPLICAZIONE (orchestrazione, senza Streamlit)                       │
│  nlda/service.py      un turno: domanda → codice → esecuzione → spieg.│
│  nlda/results.py      i TIPI d'esito (Success/Failure + kind)          │
│  nlda/export.py       conversazione → Markdown (funzioni pure)         │
└──────────────────────────────────────────────────────────────────────┘
                                   │ usa
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  DOMINIO / LOGICA                                                     │
│  nlda/agent.py        domanda → codice Pandas + narrazione             │
│  nlda/prompts/        i system prompt, versionati come file .md        │
│  nlda/loader.py       lettura file, profilo dataset, analisi automatica│
│  nlda/periods.py      confronto tra periodi (deterministico)           │
│  nlda/checks.py       controlli di fiducia sulla risposta              │
│  nlda/charts.py       costruzione e tema delle figure Plotly           │
└──────────────────────────────────────────────────────────────────────┘
                                   │ usa
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  SICUREZZA / ESECUZIONE                                               │
│  nlda/sandbox/validator.py  allowlist AST: ispeziona, non esegue       │
│  nlda/sandbox/runner.py     esegue, serializza, trasporta l'esito      │
│  nlda/sandbox/pool.py       riserva calda di worker (costo di avvio)   │
│  nlda/_sandbox_worker.py    il processo isolato                        │
└──────────────────────────────────────────────────────────────────────┘
                                   │ usa
                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│  INFRASTRUTTURA                                                       │
│  nlda/providers/      astrazione multi-LLM (Strategy+Template+Factory) │
│  nlda/config.py       configurazione centralizzata da env              │
│  nlda/log.py          logging strutturato + contesto per-turno         │
│  nlda/log_analysis.py riepilogo dei log (chiude il cerchio)            │
│  nlda/pricing.py      token → costo stimato per turno                  │
│  nlda/errors.py       gerarchia delle eccezioni previste               │
│  nlda/sanitize.py     neutralizza i dati non fidati                    │
│  nlda/utils.py        utility trasversali (formattazione, tipi)         │
│  nlda/demo.py         tetti di spesa della demo pubblica               │
└──────────────────────────────────────────────────────────────────────┘
```

**Perché questa separazione (Separation of Concerns)?** Ogni strato ha *una*
ragione per cambiare:
- cambia l'aspetto → tocchi solo presentazione;
- aggiungi un provider LLM → tocchi solo `providers/`;
- irrigidisci la sandbox → tocchi solo `sandbox/validator.py`.

Un cambiamento non si propaga a cascata: è il cuore della manutenibilità.

**Lo strato "applicazione" è la novità architetturale più importante** rispetto
alla 1.0. Prima l'orchestrazione di un turno viveva dentro `main.py`, mescolata
alle chiamate Streamlit: non era raggiungibile da un test se non avviando l'intera
app. Estraendola in `AnalysisService` (che non importa Streamlit) il turno è
diventato testabile in isolamento — e i 14 test di `test_service.py` esistono
proprio grazie a questo.

**E la seconda interfaccia è la prova che quella separazione era vera.** Quando
sopra il servizio è stata montata un'API HTTP e sopra l'API un frontend React,
**non è stata spostata una riga di logica**: `nlda/api/app.py` chiama esattamente
le stesse funzioni che chiama Streamlit — `loader` per i numeri, `AnalysisService`
per un turno, `sandbox` per eseguire. Che quel modulo sia in gran parte traduzione
da e verso JSON è il vero collaudo della stratificazione: un'architettura "a
strati" dichiarata ma non rispettata si scopre esattamente qui, quando la seconda
interfaccia obbliga a duplicare ciò che si credeva condiviso.

Il rischio che questo introduce ha un nome: **due interfacce che rispondono in
modo diverso alla stessa domanda**. È successo davvero — l'avviso
anti-allucinazione esisteva solo in Streamlit, e la demo React non lo emetteva —
ed è il motivo per cui oggi i controlli sulla risposta hanno **una sola porta**
(`checks.question_warnings`, §7.14) e i consigli d'errore una sola tabella
(`results.ADVICE`, §7.5): non per eleganza, ma perché la duplicazione qui non
produce un bug visibile, produce due verità.

### 4.2 La regola di dipendenza, e le foglie

```
                    main.py  ──►  nlda/ui/{pages,session}
                                        │
                    ┌───────────────────┼────────────────────┐
                    ▼                   ▼                    ▼
              nlda/service      nlda/ui_components      nlda/ui_theme
                    │                   │
        ┌───────────┼──────────┐        └──► charts, loader, checks, utils
        ▼           ▼          ▼
     agent      sandbox/    results
        │       runner          ▲
        ▼           │           │
   providers        ├──► sandbox/validator ──► results, errors, log
        │           └──► sandbox/pool
        ▼
   pricing, errors, config, log

   _sandbox_worker.py ──► nlda.sandbox.runner, nlda.config, nlda.log
                          (MAI main.py: nessun effetto Streamlit nel worker)
```

Osservazioni:
- **`config`, `log`, `utils`, `sanitize`, `demo`, `pricing`, `results` sono le
  foglie**: non dipendono da altri moduli del progetto (solo stdlib e pandas).
  Sono le fondamenta.
- **`ui_theme.py` non importa nulla, di proposito.** La sua `PALETTE` è letta
  anche da `charts.py`, che viene caricato dal worker della sandbox: una
  dipendenza aggiunta lì — Streamlit su tutte — entrerebbe nel costo di avvio di
  ogni esecuzione di codice.
- **`main.py` non importa mai un provider concreto**: passa solo una stringa
  (`"ollama"`, `"groq"`, …) e la factory fa il resto. Basso accoppiamento.
- **`errors.py` importa `providers.base` solo dentro un metodo** (`classify`),
  non a livello di modulo: evita un ciclo e lo tiene svincolato.

---

## 5. Il flusso dell'applicazione, passo per passo

### 5.1 Flusso di una domanda (il percorso critico)

```
UTENTE scrive "Mostrami le vendite per regione" e preme Invia
   │
   ▼
[ui/pages.render_chat]  →  AnalysisService.answer(question, df, explain=…)
   │
   ├── tutto il turno sotto un unico turn_id (bind_context + new_turn_id)
   │
   ├─► agent.ask_code(question, df)
   │       │  1. _chart_intent → serve un grafico? di che tipo?
   │       │  2. prompts.render("code_generation", …) → SCHEMA REALE del dataset
   │       │  3. provider.generate(system, user) → chiamata LLM (retry/backoff)
   │       │  4. clean_code → toglie i fence ```python
   │       │  5. _wrap_chart → se serve un grafico ma manca, avvolge in to_chart()
   │       ▼
   │   codice Pandas (stringa)
   │
   ├─► sandbox.runner.execute_pandas_code(codice, df)
   │       │  1. clean_code + validator._parse_and_validate (ALLOWLIST AST) → fail-fast
   │       │  2. riserva.esegui → worker già caldo: (code, df) picklati su stdin
   │       │        └─ worker: _limit_memory + _run_code + serialize_result
   │       │  3. _deserialize_result → ExecutionSuccess | ExecutionFailure
   │       ▼
   │   ExecutionSuccess(fig, value, summary)   OPPURE   ExecutionFailure(kind, message)
   │
   ├─► [correzione, max 3 volte, SOLO se result.retryable]
   │       agent.fix_code(question, df, codice_rotto, errore) → nuovo codice
   │       execute_pandas_code(...) di nuovo
   │
   ├─► se explain e l'esito è Success:
   │       service.stream_explanation(...) → testo a blocchi (effetto typewriter)
   │
   ▼
Turn(question, code, result, explanation) salvato in st.session_state
   │
   ▼
[render] ui/pages._render_turn → risposta, grafico/tabella, colonne toccate,
                                 avvisi di plausibilità, codice (collassato)
```

**La differenza che conta rispetto alla 1.0.** Prima il ciclo di correzione
scattava su *qualunque* errore, riconosciuto dal prefisso `"Errore"` di una
stringa. Ora scatta **solo se `result.retryable`**, cioè solo per `syntax` e
`runtime`. Un rifiuto della sandbox (`security`), un `timeout`, un provider
irraggiungibile o un ambiente non disponibile **non sono correggibili**
riformulando il codice: ritentarli bruciava chiamate all'LLM senza alcuna
possibilità di successo. È un bug reale, chiuso da un tipo.

### 5.2 Flusso di avvio / report iniziale

```
Avvio script (Streamlit ri-esegue main.py ad OGNI interazione)
   │
   ├─► configure_page() → set_page_config (deve essere la PRIMA chiamata st.)
   ├─► ui/pages.render_sidebar_config → provider, modello, api_key, file, unità
   ├─► inietta CSS (ui_theme.console_css)
   ├─► ui/session.get_agent(...) → DataAgent in st.session_state (NON cache_resource)
   ├─► riserva.prewarm() → scalda un worker mentre l'utente legge il report
   ├─► ui/session.load_dataframe → @st.cache_data
   ├─► render_join → eventuale unione con un secondo file
   ├─► render_report_selectors + render_filter → misura, categoria, filtro globale
   │
   ├─► dataset_signature(df, source_label); se cambia → azzera conversazione
   ├─► refresh_report_state → analyze(df) SOLO se la firma del report cambia
   │
   ├─► render_kpis + anteprima (a tutta larghezza)
   ├─► due colonne: [report, confronto periodi, struttura] | [chat]
   │
   └─► fill_overview(...) PER ULTIMA: è l'unica parte che aspetta il modello,
       così KPI, tabelle e grafici sono già a schermo mentre la sintesi arriva
```

**Il concetto chiave di Streamlit da capire subito:** lo script `main.py` viene
**rieseguito da capo ad ogni interazione** (ogni click, ogni input). Non è un
server con handler di eventi: è uno script che "ridisegna" tutta la pagina ogni
volta. Da qui nascono tre esigenze pervasive:

1. **Caching** (`@st.cache_data`): per non rileggere/ricalcolare tutto ogni volta.
2. **`st.session_state` + "signature"**: per ricordare cosa è già stato fatto e
   rigenerarlo *solo quando gli input rilevanti cambiano* (le variabili `*_sig`).
3. **Ordine di rendering come leva di UX**: ciò che aspetta la rete si disegna per
   ultimo, anche se compare in cima alla pagina.

---

## 6. Struttura delle cartelle

```
Natural-Lenguage-Data-Analyst/
├── main.py                      Entry-point Streamlit: SOLO il flusso della pagina
├── nlda/                        Il pacchetto importabile (era 'core/')
│   ├── config.py                Configurazione centralizzata (dataclass da env)
│   ├── log.py                   Logging strutturato + contesto per-turno
│   ├── log_analysis.py          Riepilogo dei log JSON (costo/latenza/esiti)
│   ├── utils.py                 fmt_num, clean_code, column_kind, md_safe
│   ├── sanitize.py              Neutralizza i dati non fidati (una sola difesa)
│   ├── errors.py                NLDAError, ProviderError e sottoclassi
│   ├── results.py               ExecutionSuccess/Failure + FailureKind + retryable
│   ├── demo.py                  DemoLimits: tetti per sessione e giornalieri
│   ├── pricing.py               Usage + stima del costo in USD
│   ├── loader.py                Lettura multi-formato, profilo, analisi automatica
│   ├── periods.py               Confronto tra periodi (motore deterministico)
│   ├── views.py                 Filtro e unione: viste sul dataset (funzioni pure)
│   ├── kpis.py                  Le card in cima al report, già formattate
│   ├── suggestions.py           Domande d'esempio costruite sulle colonne vere
│   ├── demo_data.py             I dataset di esempio presenti su disco
│   ├── project_qa.py            "Chiedi al progetto": recupero TF-IDF sui .md
│   ├── checks.py                Colonne toccate + mappa dichiarata + avvisi
│   ├── charts.py                Figure Plotly e tema (era in executor.py)
│   ├── agent.py                 Domanda → codice; le narrazioni
│   ├── prompts/                 I system prompt come file .md versionati
│   │   ├── code_generation.md   ├── explain.md
│   │   ├── overview.md          └── executive_report.md
│   ├── service.py               AnalysisService: orchestra un turno (senza Streamlit)
│   ├── export.py                Conversazione → Markdown (funzioni pure)
│   ├── sandbox/
│   │   ├── validator.py         Allowlist di nodi AST + _SafeModule
│   │   ├── runner.py            Esecuzione, serializzazione, trasporto
│   │   └── pool.py              Riserva calda di worker
│   ├── _sandbox_worker.py       Il processo isolato (python -m nlda._sandbox_worker)
│   ├── providers/               Astrazione multi-LLM
│   │   ├── __init__.py          Factory + registri (DEFAULT_MODELS, REQUIRES_API_KEY)
│   │   ├── base.py              LLMProvider astratto (Template Method: generate)
│   │   ├── ollama_provider.py   anthropic_provider.py  openai_provider.py
│   │   └── gemini_provider.py   groq_provider.py (eredita da OpenAIProvider)
│   ├── api/                     La SECONDA interfaccia: lo stesso backend via HTTP
│   │   ├── app.py               Le rotte FastAPI (traduzione da e verso JSON)
│   │   ├── models.py            Le forme dei dati → OpenAPI → tipi TypeScript
│   │   ├── store.py             Dove vive un dataset fra due richieste
│   │   ├── quota.py             Il tetto di spesa della demo pubblica
│   │   └── streaming.py         Un turno trasmesso a pezzi (Server-Sent Events)
│   ├── ui/
│   │   ├── pages.py             Le sezioni della pagina (render_*)
│   │   └── session.py           Plumbing: secret, quota, cache, caricamento dati
│   ├── ui_components.py         Pezzi riusabili: card KPI, riquadri, tabelle
│   └── ui_theme.py              CSS del tema (non importa NULLA, di proposito)
├── frontend/                    SPA React + TypeScript (Vite), servita dall'API
│   └── src/
│       ├── api/client.ts        Le chiamate; types.ts è GENERATO dallo schema
│       ├── api/stream.ts        Il lettore SSE (accumulatore, non split ingenuo)
│       └── components/          Chat, Report, Filtro, Unione, Periodi, Tema…
├── tests/                       34 file, 679 test
│   ├── conftest.py              fixture condivise (sales_df)
│   ├── fixtures/                corpus registrato per il replay
│   ├── test_api.py (75)         il contratto HTTP: forme, codici, avvisi
│   ├── test_loader.py (54)      test_checks.py (39)
│   ├── test_ui_components.py (31)  test_main.py (31)
│   ├── test_api_quota.py (24)   il tetto di spesa, su ENTRAMBE le rotte
│   ├── test_executor_sandbox.py regression di sicurezza
│   ├── test_validator_fuzz.py   property test (hypothesis) sul validatore
│   └── …e altri file (provider, agent, sanitize, log, export, periodi…)
├── frontend/src/test/           12 test dell'interfaccia (vitest)
├── scripts/                     Accessori (non necessari all'app)
│   ├── analyze_logs.py          eval.py         make_favicon.py
│   ├── record_corpus.py         smoke.py        verifica_deploy.py
│   ├── genera_tipi_ts.py        Schema OpenAPI → tipi TypeScript (committati)
│   ├── env_da_blueprint.py      prepara_dataset_film.py
├── data/sales.csv               Dataset di esempio (Superstore Sales, ~9.800 righe)
├── assets/                      Logo e favicon
├── docs/                        Anteprime PNG del README (screenshot reali)
├── .streamlit/                  config.toml + secrets.toml.example
├── .github/workflows/
│   ├── ci.yml                   Test (3 versioni) + lint/mypy + Docker + sicurezza
│   └── smoke.yml                Smoke test
├── Dockerfile                   Immagine dell'app (python:3.14-slim)
├── docker-compose.yml           Avvio locale con mem_limit: 2g
├── pyproject.toml               FONTE DI VERITÀ: deps, extra, ruff, mypy, pytest
├── requirements.txt             SOLO per Streamlit Cloud (vedi §3)
├── README.md                    ARCHITECTURE.md   THREAT_MODEL.md   VALUE.md
├── DEPLOY.md                    LICENSE (MIT)
└── .env.example                 Template variabili d'ambiente
```

**Perché il pacchetto si chiama `nlda` e non `core`.** Una libreria installabile
non deve occupare un namespace comune: `import core` è una collisione che aspetta
di succedere in qualunque ambiente con più pacchetti. Il rinomino è arrivato
insieme a `pyproject.toml` — cioè nel momento in cui il progetto è diventato
davvero installabile, e il nome ha smesso di essere un dettaglio interno.

---

## 7. Analisi file per file, funzione per funzione

Per ogni componente rispondo alle cinque domande: **perché esiste**, **quali
responsabilità ha**, **con chi comunica**, **quali principi applica**, **quali
alternative erano possibili**. I moduli centrali hanno una trattazione piena; per
quelli minori c'è una scheda breve — non perché contino poco, ma perché la loro
scelta progettuale si esaurisce in un paragrafo.

---

### 7.1 `main.py` — il flusso della pagina

**Perché esiste.** È il punto di ingresso dell'app (`streamlit run main.py`).
Contiene **soltanto il flusso**: cosa si mostra e in quale ordine.

**Responsabilità (e ciò che deliberatamente NON fa).** 154 righe, ed è una scelta.
Non legge file, non valida codice, non chiama LLM, non disegna componenti: il
layout delle sezioni sta in `nlda/ui/pages.py`, il plumbing in `nlda/ui/session.py`,
i numeri in `nlda/loader.py`, il turno di conversazione in `nlda/service.py`.
`main.py` è la partitura, non gli strumenti.

**Un dettaglio che vale una domanda: importare `main.py` non disegna nulla.**
`st.set_page_config` è dentro `configure_page()`, chiamata da `main()` sotto la
guardia `if __name__ == "__main__"`. Così i pezzi dell'app restano raggiungibili da
un test (`tests/test_main_history.py`, `tests/test_main.py` — 37 test in tutto) senza
che l'import faccia partire Streamlit. È il motivo per cui esiste la guardia: non
convenzione, testabilità.

**Elementi chiave.**

**Favicon con degradazione elegante.**
```python
_FAVICON = Path(__file__).parent / "assets" / "favicon.png"
page_icon=str(_FAVICON) if _FAVICON.exists() else "📊"
```
Se il PNG manca, ripiega su un'emoji: l'app parte comunque. Resta in `main.py` — e
non in `nlda/ui/` — perché il path è relativo alla **radice del repo**, dove vive
`assets/`.

**L'ordine di rendering come leva di prestazione percepita.** `fill_overview(...)` è
chiamata **per ultima**, anche se la sintesi che produce compare in cima alla
pagina. È l'unica parte che aspetta il modello: generandola alla fine, l'utente ha
già davanti KPI, tabelle, grafici e il box domanda mentre la narrazione arriva.
Streamlit permette questo con i *placeholder*: `render_report` restituisce uno slot
vuoto, che `fill_overview` riempie dopo. È una tecnica difendibile — la latenza non
si riduce, si sposta dove non si nota.

**`riserva.prewarm()`** prepara un worker della sandbox mentre l'utente legge il
report: l'avvio costa ~840 ms di import (pandas, plotly) e pagarli qui significa
non pagarli alla prima domanda. È idempotente, quindi i rerun continui di Streamlit
non lo ripetono.

**Il CSS è iniettato presto, non in un punto preciso.** Il blocco `<style>` vale per
l'intero documento a prescindere da dove finisce nel DOM: la posizione non cambia la
cascata, incide solo su quanto presto arriva al browser.

**Le due colonne (report | chat)** con scroll indipendente, ottenuto **in CSS** e non
con `st.container(height=...)`: quello resettava lo scroll in cima a ogni rerun, e
un expander sembrava "non aprirsi". Il marker invisibile `<span class='scrollcol'>`
serve al CSS per riconoscere le due colonne giuste.

**Principi:** Separation of Concerns, degradazione elegante, testabilità per
costruzione (la guardia `__main__`), ottimizzazione della latenza *percepita*.

---

### 7.2 `nlda/ui/session.py` — il plumbing della UI

**Perché esiste.** Tutto ciò che le sezioni della pagina usano ma che **non è
layout**: lettura dei secret, tetti di spesa, cache del DataFrame e dell'agente.
Separato dalle `render_*` per non mescolare "come funziona" con "come si mostra".

**`_secret(key, default)`** legge da `st.secrets` (deploy) o dalle variabili
d'ambiente (locale), con un `try/except` che non esplode se `secrets.toml` non
esiste — condizione **normale** in locale, non un errore da segnalare.
*Principio:* 12-Factor. *Perché il doppio canale:* su Streamlit Cloud i secret
vivono in `st.secrets`, in locale nell'ambiente.

**`get_agent(provider, model, api_key)` — la nota di sicurezza centrale.**
```python
# NON si usa st.cache_resource, che è una cache CONDIVISA fra tutte le sessioni:
# la API key finirebbe in memoria globale. L'agente vive in st.session_state.
```
`st.cache_resource` è globale al server: se ci mettessi l'agente, la chiave API di
un utente sarebbe raggiungibile dalle sessioni di altri utenti. In
`st.session_state` (isolato per sessione) il leak non esiste. L'agente si ricrea
solo se cambia la tupla `(provider, model, api_key)`.
**È una delle domande di sicurezza più probabili in colloquio.**

**Le funzioni `@st.cache_*` stanno a livello di modulo, di proposito.** Definirle
dentro un'altra funzione le ricreerebbe a ogni rerun: la chiave di cache
cambierebbe ogni volta e la cache non verrebbe **mai** colpita. È un errore
classico con Streamlit, e il commento nel file lo dice esplicitamente.

**`_NamedBytesIO`** è un `BytesIO` con un attributo `.name`, così `read_any`
riconosce l'estensione. `load_uploaded_cached(name, data)` memoizza sul
**contenuto** (nome + byte): rilegge solo se il file cambia davvero.

**La quota della demo, a due livelli.** `demo_allows` / `demo_consume` combinano il
contatore per sessione (`st.session_state`) con quello giornaliero
(`@st.cache_data` con TTL, condiviso fra le sessioni del server). Il perché di due
limiti sta in `demo.py` (§7.25).

**`apply_filter` / `join_datasets`** sono preprocessing: il filtro restringe
l'*intera* pagina e resta attivo tra i turni; il join produce un df unito, così il
resto della pipeline continua a lavorare su un solo DataFrame.

**Principi:** SoC, 12-Factor, sicurezza per-sessione, memoization corretta.

---

### 7.3 `nlda/ui/pages.py` — le sezioni della pagina

**Perché esiste.** Raccoglie le funzioni `render_*` che disegnano la barra laterale,
i KPI, il report, il confronto tra periodi, la struttura delle colonne e la chat. È
il modulo più lungo della UI (556 righe) perché il layout *è* verboso, ma non
contiene calcolo: i numeri li chiede a `nlda.loader`, il turno a `nlda.service`.

**`refresh_report_state(df, data_sig, …)`** è il guardiano delle firme: ricalcola
`analyze(df)` **solo** se la firma del report cambia. Le firme sono **annidate** —
`report_sig` contiene la firma del dataset, `overview_sig` contiene `report_sig` più
provider/modello/unità. Così cambiare provider rigenera la narrazione AI ma **non**
ricalcola gli insight Pandas, che dal provider non dipendono. È memoization manuale
a grana fine, e il controllo esplicito è il motivo per cui non si usa
`@st.cache_data` per tutto: le figure Plotly e gli oggetti con stato non si cacheano
sempre bene.

**`render_chat`** è il punto in cui la conversazione incontra il servizio: chiama
`AnalysisService.answer(...)` passando un `on_step` che aggiorna un'etichetta di
avanzamento ("Genero il codice…", "Eseguo il codice…"). Il servizio resta senza
Streamlit: riceve un semplice callable.

**`_cap_storico`** limita la storia mostrata: una conversazione lunga rigenererebbe
decine di widget a ogni rerun.

**`render_period_comparison`** ha una nota di prestazione interessante: Streamlit
esegue il corpo di un `expander` **anche quando è chiuso**, e `compare_periods` è un
groupby sull'intero dataset. Senza memoria, aprire e chiudere la sezione ricalcolava
tutto ogni volta.

**Principi:** SoC (layout vs calcolo), memoization esplicita a grana fine.

---

### 7.4 `nlda/service.py` — l'orchestrazione di un turno

**Perché esiste.** Questa logica viveva dentro `main.py`, mescolata alle chiamate
Streamlit: **non era raggiungibile da un test** se non avviando l'intera app. Qui
non entra nulla di Streamlit — niente spinner, niente `session_state`: il servizio
riceve un agente, restituisce un `Turn`, e la UI si limita a mostrarlo.

**`Turn`** è una dataclass congelata: `(question, code, result, explanation)`. Un
giro completo di conversazione come **valore**, non come effetto collaterale sparso
in `session_state`.

**`answer(...)` — il ciclo di correzione, corretto.**
```python
while (isinstance(result, ExecutionFailure) and result.retryable
       and attempt < self.max_retries):
```
La condizione `result.retryable` è il cuore. Vedi §7.5: solo `syntax` e `runtime`
sono correggibili. Prima si ritentava su qualsiasi errore riconosciuto dal prefisso
`"Errore"` di una stringa — quindi anche su un rifiuto della sandbox, che il
modello avrebbe riprodotto identico, e su un provider irraggiungibile, che non
c'entra col codice.

**Il `turn_id` che lega tutto.** L'intero turno gira sotto
`with bind_context(turn_id=new_turn_id())`: i log della generazione, dei tentativi
di correzione e delle chiamate al provider riportano lo stesso identificativo. Una
richiesta lenta o fallita si ricostruisce filtrando **un solo campo**. È
osservabilità utile, non log decorativi.

**`ProviderError` chiude il turno subito.** Se il modello è irraggiungibile non c'è
codice da correggere: si ritorna un `ExecutionFailure("provider", …)` senza
bruciare tentativi.

**`stream_explanation`** restituisce un generatore che la UI consuma con
`st.write_stream` (effetto typewriter). Anche qui: il servizio non conosce
Streamlit, produce solo blocchi di testo.

**Principi:** Separation of Concerns, testabilità (13 test in `test_service.py`),
Dependency Injection (riceve l'agente), inversione della dipendenza da Streamlit
(il callback `on_step` invece di chiamare la UI).
**Alternativa:** lasciare tutto in `main.py` — funziona, ma il turno diventa
verificabile solo a mano, cliccando.

---

### 7.5 `nlda/results.py` — l'esito come TIPO

**Perché esiste.** È il modulo più istruttivo del refactoring, e la storia è questa:
prima l'esito viaggiava come `dict | str`, e il fallimento si riconosceva dal
**prefisso "Errore"** del messaggio. Il control flow dipendeva da una stringa in
italiano — cioè da un testo pensato per l'utente finale. **Bastava riformulare un
messaggio per cambiare, in silenzio, il comportamento dell'app.**

**La soluzione.** L'esito è un tipo: `ExecutionSuccess` o `ExecutionFailure`. Il
chiamante distingue i due casi **sul tipo**, e sul fallimento legge `kind` — la
CAUSA, non il testo.

```python
FailureKind = Literal["syntax", "security", "runtime", "dependency",
                      "timeout", "provider", "internal"]
FAILURE_KINDS = get_args(FailureKind)   # le stesse cause, ispezionabili a runtime
```

**`retryable` — dove il tipo ripaga.**
```python
@property
def retryable(self) -> bool:
    return self.kind in ("syntax", "runtime")
```
Un errore di sintassi o un'eccezione sui dati sono un difetto del codice generato:
rigenerarlo ha senso. Un rifiuto della sandbox, un timeout, un provider
irraggiungibile o un ambiente non disponibile **non dipendono dalla formulazione
del codice**: ritentare brucerebbe solo chiamate all'LLM. Questa property chiude un
bug reale, e lo chiude in un punto solo.

**`dependency` è l'ultima causa aggiunta, e spiega perché l'elenco vive qui.**
Chiedendo una linea di tendenza il modello genera `trendline='ols'`, che in Plotly
richiede `statsmodels` — non installato, perché si porta dietro `scipy` e il
container della demo ha 512 MB in tutto. Il fallimento era classificato `runtime`,
quindi **ritentabile**: tre chiamate al modello per rigenerare un codice che al
secondo tentativo avrebbe trovato la libreria assente esattamente come al primo.
Spesa certa, successo impossibile. E l'utente leggeva «forse una colonna non esiste
con quel nome», che è falso e lo manda a cercare un problema che non ha.
Separare la causa ha sistemato entrambe le cose **senza toccare né il retry né la
UI**: la property e la tabella dei consigli leggono da qui.

**`ADVICE` — cosa può farci l'utente, per ogni causa.** Il `message` di un
fallimento descrive il guasto («uso di `open` non consentito»); `ADVICE` dice come
uscirne. Sta accanto a `FailureKind` e non nel componente che lo mostra perché
prima viveva **solo nel client React**: la stessa sandbox che rifiutava lo stesso
codice dava un consiglio in un'interfaccia e il testo grezzo nell'altra.

**Dettaglio da notare:** `FAILURE_KINDS` è derivato dal `Literal` con `get_args`,
non riscritto a mano. Serve a validare ciò che arriva dal sottoprocesso senza
mantenere una seconda copia dell'elenco — che è esattamente il tipo di duplicazione
che si desincronizza.

**`EXECUTED_OK`** è la costante per "il codice è andato a buon fine ma non ha
prodotto né valore né figura". Vive qui, con gli altri tipi d'esito, perché più
moduli la riconoscono: tenerne copie della stringa in giro significa che basta
riformularne una perché gli altri smettano di riconoscerla e ricomincino a
stamparla all'utente.

**Principi:** *Make illegal states unrepresentable*, tipi al posto di stringhe
(*stringly-typed* è un anti-pattern), Single Source of Truth.
**Alternativa:** codici d'errore interi o eccezioni per ogni caso. Le eccezioni
sono usate altrove (`errors.py`) ma non qui: l'esito di un'esecuzione **attesa** è
un valore di ritorno normale, non un evento eccezionale (vedi la nota di confine in
`errors.py`).

---

### 7.6 `nlda/errors.py` — le eccezioni previste

**Perché esiste.** Stessa lezione di `results.py`, sul versante di ciò che viene
*sollevato*: un guasto ha bisogno di un **tipo**, non di un messaggio. Sul tipo si
decide (si ritenta? si mostra? si blocca?); il messaggio serve solo a chi legge.

**`NLDAError`** è la radice comune. Permette a un chiamante di distinguere un guasto
**previsto** dall'applicazione da un **bug** di programmazione, che deve continuare
a propagarsi invece di essere inghiottito da un `except Exception`. Vi appartiene
anche `UnsafeCodeError`.

**`ProviderError.classify(provider, original)`** è una **factory di eccezioni**:
scegle la sottoclasse giusta dal guasto originale, così il chiamante non deve
conoscere gli status HTTP dei vari SDK.
- 401/403 → `ProviderAuthError` ("controlla la API key": né retry né riformulazione
  aiutano, deve agire l'utente);
- 408/504, o nome del tipo che contiene `timeout`/`connection` →
  `ProviderTimeoutError` ("riprova tra poco": qui riprovare può riuscire);
- tutto il resto → `ProviderError` generico.

Il fallback sul **nome del tipo** (`APITimeoutError`, `APIConnectionError`,
`TimeoutError`) è la parte difendibile: per gli errori senza risposta HTTP è
l'unico segnale disponibile, ed è indipendente dall'SDK.

**La nota di confine, importante.** Non tutti i fallimenti passano da qui: la
pipeline di esecuzione del codice generato comunica il proprio esito con il
**risultato tipizzato** `ExecutionFailure`, non sollevando eccezioni. Le eccezioni
di questo file servono ai guasti che **attraversano più livelli** — su tutti quello
del provider. Sapere *perché* due meccanismi coesistono è una risposta migliore che
saperli elencare.

**Principi:** gerarchia di eccezioni tipizzate, messaggi azionabili, separazione tra
guasto previsto e bug.

---

### 7.7 `nlda/config.py` — configurazione centralizzata

**Perché esiste.** Prima i parametri (timeout, limiti, retry) erano sparsi tra
executor, worker, provider e main. È la **fonte unica di verità** per la
configurazione di runtime.

**`Settings` è `frozen=True`.** La config si **legge**, non si muta a metà
esecuzione. Se fosse mutabile, un modulo potrebbe cambiarla e alterare il
comportamento di un altro in modo imprevedibile (accoppiamento nascosto).
L'immutabilità la rende un valore prevedibile e sicuro da leggere.

| Variabile | Default | Effetto |
|-----------|---------|---------|
| `SANDBOX_SUBPROCESS` | true | esecuzione in sottoprocesso isolato |
| `EXEC_TIMEOUT` | 12 | secondi di wall-time per il codice generato |
| `MEMORY_LIMIT_MB` | 1500 | cap RAM del worker (solo POSIX) |
| `ALLOW_INPROCESS_FALLBACK` | true | fallback in-process se il subprocess non parte |
| `MAX_ROWS` | 2.000.000 | tetto di **usabilità** sul file caricato |
| `MAX_COLUMNS` | 500 | idem |
| `MAX_DATASET_RAM_MB` | 256 | quanta memoria può occupare **un** dataset letto |
| `MAX_STORE_RAM_MB` | 256 | tetto di RAM sui dataset che l'API tiene in memoria |
| `LLM_REQUEST_TIMEOUT` | 30.0 | timeout singola chiamata LLM |
| `LLM_MAX_RETRIES` | 1 | tentativi extra oltre al primo |
| `LLM_RETRY_BACKOFF` | 0.8 | base del backoff esponenziale |
| `LOG_LEVEL` | INFO | verbosità |
| `LOG_FORMAT` | text | `text` per sviluppo, `json` per produzione |

**`MAX_STORE_RAM_MB` è il limite di memoria, e sta altrove per un motivo.** `MAX_ROWS`
governa *un* file; questo governa la *somma* dei dataset che l'API tiene fra una
richiesta e l'altra. Il magazzino aveva già un tetto sul **numero** di tabelle (8),
che non è un tetto di RAM: otto file entro `MAX_ROWS` fanno 2,7 GB, più del container
da 2 GB e cinque volte i 512 MB del piano su cui gira la demo. Ora si contano i byte
(`memory_usage(deep=True)`, misurato una volta per caricamento) e si sfratta il meno
usato di recente. Un singolo dataset più grande del tetto **si tiene** — buttarlo
lascerebbe l'utente senza il file appena caricato, e la RAM sarebbe occupata comunque
— ma emette un `magazzino_oltre_il_tetto` nei log: un tetto tarato male si deve poter
vedere.

**`MAX_DATASET_RAM_MB` è il tetto che tiene in vita il processo.** Gli altri due
governano quanto si *tiene*; questo governa quanto si può *leggere*, ed è nato da un
guasto riprodotto in produzione il 5 agosto 2026: un CSV da 20 MB su disco — 50.000
righe per 200 colonne di interi, dentro `MAX_ROWS` e dentro `MAX_COLUMNS` — diventa
una tabella da 80 MB e ne chiede **207 al processo** mentre la costruisce. Sul
container da 512 MB della demo il caricamento veniva ucciso dal sistema operativo, e
con il processo sparivano i dataset di **tutti** i visitatori. Nessuno dei tetti
esistenti poteva vederlo: righe e colonne non misurano i byte, e il tetto del
magazzino si applica quando la tabella esiste già, cioè dopo che la memoria è stata
presa. Ora il costo si **stima sul primo megabyte** ed estrapola (misurato: 76 MB
stimati contro 80 reali), e il file si rifiuta prima di allocarlo. Per i formati che
non si stimano — `.xlsx` è compresso, il JSON va letto tutto — il controllo resta
dopo la lettura: non evita il picco, evita di tenere occupata la memoria.

**`MAX_ROWS` non è un limite di memoria, ed è dichiarato.** Il commento nel codice
lo dice: 3 milioni di righe costano ~340 MB di picco, dentro i 2 GB del container.
È un limite di **usabilità** — oltre quelle soglie ogni domanda richiede più di un
secondo e il report iniziale diversi, e un'app che risponde così non è un'app che si
vuole mostrare. Nota fine: il cap di upload di Streamlit (25 MB) bounda già i CSV,
**ma non i formati compressi** — un `.xlsx` da 25 MB può contenere milioni di righe.

**I limiti della demo NON stanno qui**, ed è una scelta annotata: arrivano dai
secrets di Streamlit, non dall'ambiente, e vivono in `nlda.demo.DemoLimits`. Tenerne
una copia anche qui significava avere due fonti di verità, di cui una mai letta da
nessuno.

**Gli helper `_env_int / _env_float / _env_bool`** leggono da env con **fallback
robusto**: se la variabile è malformata (`EXEC_TIMEOUT=abc`) tornano al default
invece di far crashare l'app.

**Principi:** Single Source of Truth, 12-Factor, Fail-Safe Defaults, immutabilità.
**Alternativa:** un `.ini`/`.yaml`; ma le env sono lo standard per il deploy cloud e
non richiedono parsing. Una dataclass dà anche type-safety.

---

### 7.8 `nlda/log.py` — logging strutturato

**Perché esiste.** In produzione/demo il logging è l'unico modo per capire perché una
domanda è fallita: quale regola della sandbox ha bocciato il codice, quanto ha
impiegato il provider, quanto è costato il turno.

**Due formati, un interruttore.** `LOG_FORMAT=text` dà una riga leggibile per lo
sviluppo; `json` dà **una riga JSON per riga**, pensata per l'ingestione in un
aggregatore. Il secondo è ciò che rende possibile `log_analysis.py`: senza un
formato macchina, "riassumi i costi della giornata" resta un lavoro manuale.

**Il contesto per-turno.** `bind_context(turn_id=…)` è un context manager che usa
`contextvars`: ogni record emesso dentro il blocco porta il `turn_id`, senza che le
funzioni intermedie debbano passarselo come parametro. È il modo pulito di
correlare log che attraversano moduli diversi.

**L'idempotenza della configurazione.**
```python
_configured = False
def _configure_once():
    global _configured
    if _configured: return
    root = logging.getLogger("nlda")   # namespace dedicato
    root.propagate = False             # non risale al root logger di terzi
```
*Perché una sola volta:* Streamlit ri-esegue lo script di continuo; senza la
guardia, ogni run aggiungerebbe un handler → righe di log duplicate, in crescita.
*Perché namespace + `propagate=False`:* per non sporcare né essere sporcati dai
logger di librerie terze (Streamlit, urllib3…).

**Principi:** idempotenza, isolamento dei namespace, log strutturati, correlazione
via contesto. **Alternativa:** `logging.basicConfig` sparso nei moduli — handler
duplicati e configurazione incoerente.

---

### 7.9 `nlda/log_analysis.py` — chiudere il cerchio dell'osservabilità

**Perché esiste.** Non basta *loggare* costo, latenza ed esito: se nessuno li
aggrega, sono righe che nessuno legge. Questo modulo trasforma i log JSON in un
riepilogo: conteggi per esito, percentili di latenza, costo totale.

**`parse_lines`** tollera le righe non-JSON (una demo mescola formati) invece di
esplodere. **`_percentiles`** è calcolato a mano su una lista ordinata: nessuna
dipendenza aggiuntiva per una mediana.

Usato da `scripts/analyze_logs.py`. Sei test in `test_log_analysis.py`.

**Principi:** osservabilità come ciclo chiuso, robustezza sull'input.

---

### 7.10 `nlda/utils.py` — utility condivise

**Perché esiste.** Evitare duplicazione di logica trasversale. È una foglia (dipende
solo da `re`, `warnings` e `pandas`).

**`fmt_num(x)`** formatta all'italiana: migliaia `.`, decimale `,`. Il trucco è lo
swap con un carattere-ponte:
```python
return s.replace(",", "§").replace(".", ",").replace("§", ".")
```
*Perché il ponte:* senza di esso la seconda `replace` ri-toccherebbe ciò che la
prima ha appena scritto. **Perché non il modulo `locale`:** dipende dalle locale
installate sul sistema, fragile nei container; la formattazione manuale è
deterministica ovunque.

**`to_datetime_quiet`** è `pd.to_datetime(errors="coerce")` senza il rumore di
*"Could not infer format"*. Il loader prova a interpretare come data **ogni**
colonna testuale, quindi il fallback per-elemento di pandas è **voluto** e l'avviso
è solo rumore (circa uno per colonna all'avvio). Nota di design: si spegne **qui**,
dove la scelta è deliberata, non con un filtro globale che nasconderebbe anche gli
avvisi legittimi altrove.

**`md_safe(text)`** neutralizza due sorprese dei modelli viste in chat: i `$` della
valuta (una **coppia** di `$` è delimitatore LaTeX, e Streamlit rende quel tratto
come formula in corsivo con le parole appiccicate) e i backtick sparsi. Va applicata
**solo** dove si rende markdown: `answer_card` è HTML e lì escapare `$` produrrebbe
un `\$` visibile.

**`with_unit(text, unit)`** antepone l'unità di misura al testo per l'LLM: il
modello non ha modo di conoscerla e altrimenti la inventerebbe.

**`column_kind(series)`** classifica in `booleana / data / numerica / testo`.
**L'ordine dei controlli conta:** bool **prima** di numeric, perché in pandas `bool`
è sottotipo di numeric e senza questo controllo un flag `True/False` verrebbe
trattato come misura da sommare.

**`IT_NUM_FORMAT`** è il dizionario usato da `Styler.format`, così tabelle e testo
condividono la convenzione.

**Principi:** DRY, determinismo, sopprimere il rumore alla fonte giusta.

---

### 7.11 `nlda/sanitize.py` — i dati non fidati

**Perché esiste, e perché è **una** funzione.** Prima ce n'erano **due quasi
identiche**, in `agent.py` e in `loader.py`. Due copie della stessa difesa
significano che un rafforzamento ne raggiunge una e dimentica l'altra — «ed è
esattamente ciò che era successo». Questa frase, nel docstring del modulo, è la
miglior risposta possibile alla domanda "perché unificare?".

**Il vettore.** Tutto ciò che proviene dal file caricato è dato ostile per
definizione: nomi di colonna e valori di cella finiscono nel testo che si manda al
modello, e un testo che finisce in un prompt può contenere **istruzioni**.

**Cosa rimuove, e perché ogni categoria conta:**

| Categoria | Perché è un rischio |
|-----------|---------------------|
| caratteri di controllo | spezzano la struttura del prompt, possono terminare un valore |
| a capo e tab | un valore con un a capo simula una nuova riga di istruzioni nello schema |
| unicode invisibile (zero-width, BOM) | non si vedono rileggendo il file, ma il modello li legge |
| override bidirezionali | ciò che una persona legge e ciò che il modello riceve diventano due cose diverse |
| backtick | chiudono e riaprono i blocchi di codice |
| metacaratteri di link Markdown (opz.) | un valore in una card potrebbe iniettare un link cliccabile |

**Il dettaglio più raffinato del progetto.** I codepoint sono scritti come
**sequenze di escape**, mai come caratteri letterali:
```python
# nel sorgente della sandbox: la notazione, mai il carattere
"\u202a-\u202e"     # override bidirezionale
"\u200b-\u200f"     # zero-width e marcatori di direzione
```
Scriverli letteralmente metterebbe caratteri bidirezionali **dentro il file**:
è la vulnerabilità **Trojan Source** (CVE-2021-42574), in cui il sorgente si *legge*
diverso da come si *esegue*. Bandit la segnala come B613, e l'ha segnalata proprio
qui — nel modulo che difende dagli invisibili. Il commento nel codice lo racconta.

**Il troncamento è una difesa, non solo economia di token:** limita quanto testo un
file ostile può iniettare in una singola cella. 40 caratteri per un valore (serve a
dare il *tipo*, non il contenuto), 60 per un nome di colonna (che il modello deve
poter riconoscere).

**Principi:** una sola difesa in un punto solo, difesa in profondità, consapevolezza
delle vulnerabilità della *toolchain* e non solo del runtime. 23 test.

---

### 7.12 `nlda/loader.py` — ingestione e analisi dei dati

**Perché esiste.** Trasforma un file grezzo di schema sconosciuto in un DataFrame
pulito e in **insight quantitativi** pronti da mostrare/narrare. È il "contabile"
del principio §2: qui si calcolano i numeri. È il modulo più grande (659 righe).

**Responsabilità.** (1) leggere multi-formato; (2) pulire e tipizzare (date, numeri
formattati, JSON annidati); (3) profilare; (4) distinguere *misure* da
*identificatori*; (5) calcolare classifiche, trend, correlazioni, insight.

#### Lettura resiliente

**`read_any`** riconosce il formato dall'estensione: `.xlsx/.xls` → primo foglio;
`.json` → `json_normalize` + `_stringify_complex`; altro → `_read_csv_resilient`.

**`_read_csv_resilient`** rileva il separatore e gestisce encoding non-UTF8
riprovando in `latin-1`. **`_detect_sep`** è nato da un bug vero: un CSV a **colonna
singola** veniva spezzato dallo sniffer di pandas, che trovava un separatore
plausibile dove non c'era.

**I byte vanno al parser così come sono.** Prima il file veniva decodificato in una
stringa, la stringa avvolta in uno `StringIO` e il tutto dato a `engine="python"`:
tre copie e il motore lento, per un picco di **459 MB** su un CSV da 20 MB. Ora
legge il parser predefinito, quello in C, direttamente dai byte: stesso risultato,
**207 MB**. Il motore python non serviva — il separatore lo sceglie `_detect_sep` ed
è sempre un carattere solo, l'unico caso che l'avrebbe richiesto. La decodifica
completa sopravvive solo dove serve davvero: contare le righe fino alla prima
irregolare, cioè su un file che stiamo comunque per rifiutare.

**`_stima_byte_csv`** legge il primo megabyte, ne misura il costo in memoria e lo
estrapola al resto del file: è ciò che permette a `MAX_DATASET_RAM_MB` di dire di no
**prima** di allocare. Restituisce `None` sui file più corti del campione, dove non
c'è nulla da estrapolare e il controllo dopo la lettura è sicuro e più preciso.

**`_check_dimensioni`** applica `MAX_ROWS`/`MAX_COLUMNS` con un messaggio chiaro,
e rifiuta anche i due casi degeneri: **zero colonne** e **zero righe** (un file con
la sola intestazione). Accettare il secondo significherebbe mostrare un report di
sole caselle vuote e lasciare all'utente il compito di capire perché.

#### Quando il file non è quello che dice di essere

Un caricamento che fallisce con un messaggio è un fastidio; un caricamento che
**riesce sul file sbagliato** è un difetto grave, perché produce numeri con l'aria
di essere giusti. Tre difese, tutte nate riproducendo il caso:

**`_rifiuta_se_binario`** ferma i formati binari travestiti da CSV riconoscendone
la **firma** nei primi byte (PDF, ZIP/`.xlsx`, vecchio Office, PNG, JPEG, SQLite) e,
come rete di sicurezza, i **byte NUL** — un file di testo non ne contiene. Prima un
PDF rinominato `.csv` non dava errore: `read_csv` ne leggeva i byte e produceva una
tabella con una colonna chiamata `%PDF-1.4`. Il messaggio **nomina** il formato
trovato, così chi ha sbagliato file lo capisce senza indovinare.

**`_rifiuta_se_disallineato`** coglie la riga con **più campi dell'intestazione**.
Pandas non la scarta e non solleva: usa i campi in eccesso come **indice**, e da lì
in poi ogni colonna contiene i valori di quella accanto. Il segnale è
deterministico — `read_csv` qui non riceve mai `index_col`, quindi un indice che non
sia il progressivo può venire solo da quell'inferenza. Si **rifiuta** invece di
avvisare: con le colonne disallineate non esiste una lettura onesta da mostrare
accanto all'avviso. Le righe con *meno* campi restano valide (valore mancante in
coda: pandas mette `NaN` e nulla si sposta).

**I messaggi di pandas si traducono.** «No columns to parse from file» parla di
*parsing* a chi vuole sapere cosa manchi al file; «Excel file format cannot be
determined, you must specify an engine manually» parla di *engine*. E poiché lo
stesso difetto pandas lo tratta in due modi (indice inferito **oppure**
`ParserError`), i due percorsi convergono su **un solo** messaggio italiano, che
nomina la riga da correggere — contata sull'**intestazione**, non sul DataFrame già
disallineato: contarne le colonne faceva additare la riga 1, l'unica sicuramente
giusta.

**`_maybe_parse_dates`** converte le colonne testuali che "sembrano" date con logica
a **doppia soglia**: se il nome contiene un indizio (`date/data/time`) basta che
≥50% dei valori sia parsabile; altrimenti serve ≥90%. *Perché:* evita che ID o
codici numerici vengano scambiati per date.

**`_maybe_parse_numbers` / `_numero_da_valuta`** riconoscono **valuta formattata**
(`"1.234,56 €"`, `"$ 1,234.56"`) e misure con mancanti indicati **a testo**
(`"n.d."`, `"-"`). È l'aggiunta più recente al loader: senza, una colonna di
fatturato letta come testo non era una misura e il report la ignorava.

#### Misure, identificatori, categorie

**`measure_columns` / `_is_identifier` / `_is_year_like`** — logica sottile e molto
"difendibile". Non tutte le colonne numeriche sono *misure* da sommare:
- un **CAP** o un **Customer ID** sono numeri, ma sommarli è insensato;
- un **anno** (2020, 2021) è una dimensione temporale, non una misura.

`_is_identifier` esclude una colonna numerica se: (a) il nome combacia con
`id/code/zip/cap/isbn/…`; (b) è "year-like" (interi 1900-2100, bassa cardinalità);
(c) su dataset ≥20 righe ha oltre il 90% di valori distinti (è di fatto una chiave).
C'è anche `_is_strong_id`, che serve al caso limite di un dataset con **un solo**
identificatore numerico: prima veniva sommato in mancanza d'altro, producendo un KPI
assurdo.
*Perché conta:* se sommassi i CAP nei KPI otterresti numeri senza significato.

**`category_columns` / `best_category`** scelgono una colonna categoriale con
cardinalità utile (2-30 distinti), preferendo per nome prima le dimensioni
geografiche (`region/country/city`), poi quelle di business
(`category/segment/type`). Un buon default rende il report utile **senza** che
l'utente configuri nulla.

#### Analisi quantitativa

**`monthly_trend`** costruisce la serie mensile (somma della misura o conteggio dei
record se `measure=None`), normalizzando con `dt.to_period("M")`.

**`_correlations(df, measures, soglia=0.6)`** — Pearson tra le misure, con guardie
importanti: solo con ≥2 misure e ≥10 righe; `min_periods=10` (servono ≥10 righe
sovrapposte non-NaN per coppia, altrimenti `r` è rumore); scarta righe/colonne
interamente NaN.

**`_findings(df, res, main_num)`** — gli **insight automatici**, cuore del §2.
Calcolati in Pandas, mai LLM. Dettagli difendibili:
- la quota del leader si calcola **solo** se il totale è positivo e la quota è
  0-100%: con misure che possono essere negative (Profit con perdite) la percentuale
  non avrebbe senso;
- segnala *"(ultimo periodo parziale)"* se l'ultimo mese non è coperto fino alla
  fine (`_last_period_partial`), perché una variazione su un mese a metà è
  fuorviante;
- outlier via **IQR** (metodo di Tukey: `Q1 − 1.5·IQR`, `Q3 + 1.5·IQR`), solo con
  ≥20 righe.

**`analyze(df, measure, category)`** è il direttore d'orchestra, con **due
modalità**: con misura numerica → statistiche, classifica, andamento; senza misura →
conteggi e distribuzioni. Restituisce un dict con chiavi opzionali; il campo `text`
(`_insights_text`) è il riepilogo dei numeri passato all'LLM.

**`_clean_label`** sanitizza i valori prima che entrino in quel testo — delegando a
`nlda.sanitize` (§7.11), non con una copia locale.

**`dataset_signature(df, label)`** produce la firma che decide quando azzerare la
conversazione: cambia il file → nuova conversazione; cambia solo il filtro → la
conversazione resta.

**`load_dataset()`** carica il dataset di esempio passandolo per la **stessa
pipeline** dei file utente: il default non è un caso speciale.

**Principi:** Single Source of Truth numerica, robustezza (doppie soglie, guardie
statistiche), sicurezza (sanitizzazione delegata), adattività.
**Alternativa alle euristiche:** chiedere all'utente di mappare a mano
misure/categorie/date — più preciso ma pessima UX. Le euristiche danno un buon
default *con* override manuale nella sidebar. 47 test.

---

### 7.13 `nlda/periods.py` — il confronto tra periodi

**Perché esiste.** È un esempio didattico del principio §2. Il confronto di una
misura tra periodi consecutivi (mese/trimestre/anno) con variazione percentuale è
un calcolo che il modello **potrebbe** scrivere ogni volta — e ogni volta in modo
un po' diverso, con un modo diverso di sbagliare i bordi.

Qui invece è un **motore deterministico condiviso da due strade**: la sandbox lo
espone al codice generato (`compare_periods(df, ...)` è un nome disponibile
nell'ambiente), e la UI lo chiama direttamente per la sezione "Confronto tra
periodi". Un'unica implementazione, testata (6 test): il modello non reinventa il
calcolo, e la UI non lo duplica.

**Dettagli difendibili:** `variazione_%` è NaN sul primo periodo (che non ha un
prima) — non zero, che sarebbe una bugia; accetta sia i nomi italiani (che usa la UI
e che il modello legge nel prompt) sia i codici brevi di pandas; solleva
`ValueError` con un messaggio chiaro se la frequenza è ignota o se la colonna non
contiene date riconoscibili.

**Principi:** Single Source of Truth per un calcolo, riuso fra layer diversi,
determinismo al posto della generazione.

---

### 7.14 `nlda/checks.py` — dare all'utente modo di fidarsi

**Perché esiste.** Il punto debole di un'app che genera codice con un LLM è la
risposta **"eseguibile ma sbagliata"**: Pandas non solleva niente, ma il numero è
quello di una domanda diversa. Nessun controllo automatico può rilevarlo in
generale — servirebbe conoscere l'intento. Questo modulo non ci prova: offre due
appigli concreti perché l'utente possa **fidarsi o dubitare** senza rileggere il
codice.

**`columns_referenced(code, columns)`** riporta quali colonne del dataset il codice
ha davvero toccato, estraendo i literal di stringa dall'AST e intersecandoli con i
nomi reali. Così l'utente vede *su cosa poggia* la risposta.
Due note oneste, entrambe nel docstring: non riporta nulla che non sia una colonna
(**zero falsi positivi**), ma **può mancare** un accesso per attributo (`df.Sales`)
— accettabile perché il prompt insegna la forma `df['Sales']`.
Dettaglio fine: `ast.walk` è in ampiezza, non in ordine sorgente, quindi le colonne
si riordinano per posizione perché `df.groupby('Region')['Sales']` dia
`[Region, Sales]` come **si legge**.

**`unknown_columns_referenced`** guarda il *codice*: le colonne che il codice generato
**legge** da `df` e che non esistono. È la stessa causa di un `KeyError`, ma còlta
**prima** di eseguire — così l'app non calcola su una colonna fantasma e il messaggio
elenca le colonne vere, guidando la correzione.

**`claimed_missing_columns`** guarda la *domanda*: i nomi che l'utente presenta
esplicitamente come colonne (`df['X']`, «la colonna Fatturato») e che non esistono.
È il segnale dietro l'avviso anti-allucinazione, e resta **lessicale**: cattura solo
ciò che l'utente ha marcato come colonna, quindi niente falsi positivi.

**`declared_mapping` / `mapping_warnings`** chiudono il caso che il lessicale non può
vedere: la grandezza chiesta con un **nome-concetto nudo** («qual è il profitto?») su
un dataset che non ce l'ha. Lì il modello tende a rispondere con una colonna simile —
`Sales` — e a spacciarla per quella chiesta: un numero giusto per una domanda che
nessuno ha fatto. Distinguere `vendite → Sales` (traduzione legittima) da
`profitto → Sales` (sostituzione) è **semantico**, e l'unico che ha quel contesto è il
modello: la **regola 10** del prompt gli impone quindi di dichiarare la mappa
termine→colonna come commento (`# mappa: profitto -> NESSUNA`), e qui la si verifica
contro le colonne vere. Due avvisi, entrambi certi: la grandezza dichiarata assente, e
la colonna dichiarata ma inesistente. **Non** si segnala il termine mappato su un nome
diverso — sarebbe rumore su quasi ogni domanda.
Che restino **commenti** è una scelta: non toccano l'esecuzione (l'AST li ignora),
viaggiano dentro `code` fino a entrambe le interfacce senza aggiungere un campo a
`Turn`, e l'utente li legge nel pannello del codice generato.

**`sanity_warnings`** sono pochi segnali ad **alta confidenza**: una quota fuori da
0–100%, un risultato NaN, una tabella vuota. **Conservativi di proposito** — «un
avviso che grida al lupo troppo spesso viene ignorato». È la frase che difende la
scelta di *non* aggiungerne altri.

**`question_warnings`** è l'unica **porta** da cui entrambe le interfacce prendono gli
avvisi sulla domanda. Non è un dettaglio di stile: l'avviso anti-allucinazione era
nato dentro la UI di Streamlit, e l'API — quindi la demo React — non lo emetteva
affatto. La stessa domanda giudicata in due modi a seconda dell'interfaccia.

**`explanation_is_redundant`** decide quando *non* chiedere la narrazione al modello.
Provando la demo il 5 agosto 2026, alla domanda «qual è il profitto per regione?» su
un dataset che il profitto non ce l'ha, la stessa informazione compariva **tre volte**:
la frase onesta che il modello mette in `result`, l'avviso deterministico, e una
spiegazione dell'AI che riformulava entrambi — la sola delle tre a costare una chiamata
e un'attesa. Si tace solo quando ricorrono **insieme** un avviso su domanda↔codice e un
risultato **testuale**, cioè già una frase compiuta: davanti a un numero o a una tabella
la spiegazione è il valore che l'utente viene a cercare, e sopprimerla sarebbe il
difetto opposto. Le tre strade che possono narrare — Streamlit, `/ask` e lo streaming,
che la spiegazione se la genera da sé — chiamano tutte questa funzione: se il giudizio
vivesse nel servizio, la demo React continuerebbe a pagare ciò che Streamlit non paga
più. Un test per strada, tutti e tre rossi se si annulla la condizione.

**Funzioni pure, nessuno Streamlit:** la UI le chiama, ma si testano da sole. 39 test.

**Principi:** onestà sui limiti dello strumento, alta precisione preferita all'alta
copertura, purezza.

---

### 7.15 `nlda/providers/` — astrazione multi-LLM

**Perché esiste.** L'app deve funzionare con provider diversi (Ollama locale, Groq,
Anthropic, OpenAI, Gemini) senza che il resto del codice sappia *quale* sta usando.
È il punto in cui il progetto applica i design pattern più "da manuale":
**Strategy**, **Template Method** e **Factory**. Preparati a discuterli.

#### `base.py` — `LLMProvider`

```python
class LLMProvider(ABC):
    ENV_VAR: "str | tuple[str, ...] | None" = None

    def generate(self, system_prompt, user_prompt) -> str:   # TEMPLATE METHOD
        attempts = max(1, settings.max_retries + 1)
        for i in range(1, attempts + 1):
            try:
                text, usage = self._call(system_prompt, user_prompt)  # passo variabile
                log.info("provider_call_ok", extra={... latenza, token, costo ...})
                return text
            except Exception as e:
                if not _is_transient(e) or i == attempts:
                    raise ProviderError.classify(self.name, e)
                time.sleep(settings.retry_backoff * (2 ** (i - 1)))   # backoff esp.

    @abstractmethod
    def _call(self, system_prompt, user_prompt): ...
```

**Il Template Method (da spiegare bene).** `generate()` definisce lo *scheletro
invariante* — retry, backoff esponenziale, logging di latenza/token/costo,
classificazione dell'errore — e lascia **un solo passo variabile**, `_call()`, alle
sottoclassi. La logica di resilienza vive in **un posto solo** invece di essere
copiata (o dimenticata) in ogni provider. Aggiungi un provider e ottieni
retry+timeout+logging+costo *gratis*.

**La differenza rispetto alla 1.0:** ora `generate` **non ritenta gli errori non
transitori** e solleva `ProviderError.classify(...)` invece dell'eccezione grezza
dell'SDK. Ritentare un 401 è tempo perso: la chiave sbagliata resta sbagliata.

**Backoff esponenziale:** `retry_backoff * 2^(i-1)` → 0.8s, 1.6s, 3.2s… Se il
servizio è sotto carico, ritentare subito peggiora; distanziare i tentativi gli dà
tempo di riprendersi (evita il *thundering herd*).

**`_http_status(exc)`** è **l'unica** estrazione di status HTTP del progetto, ed è
riusata da `errors.ProviderError.classify`: una sola funzione che sa dove i vari SDK
mettono il codice di stato.

**`_key_from_env()`** risolve la API key da una **stringa o tupla** di nomi env
(Gemini accetta sia `GOOGLE_API_KEY` sia `GEMINI_API_KEY`). I provider concreti non
toccano `os.getenv`.

#### I provider concreti — quanto poco codice serve

- **`ollama_provider.py`** (locale, nessuna key): `ENV_VAR = None`. Implementa anche
  `stream`.
- **`anthropic_provider.py`**: `ENV_VAR="ANTHROPIC_API_KEY"`. Nota difendibile: non
  passa `temperature`/`top_p` perché su alcuni modelli recenti i parametri di
  sampling sono stati rimossi e restituiscono 400; la determinazione si guida via
  prompt.
- **`openai_provider.py`**: `ENV_VAR="OPENAI_API_KEY"`, con `base_url` opzionale.
- **`groq_provider.py`**: **eredita da `OpenAIProvider`** e cambia solo `ENV_VAR` e
  `base_url`. L'API di Groq è compatibile con l'SDK OpenAI → **zero codice
  duplicato, zero dipendenze in più**. Ottimo esempio di riuso per ereditarietà (ed
  è un **Adapter** nella sostanza).
- **`gemini_provider.py`**: `ENV_VAR=("GOOGLE_API_KEY","GEMINI_API_KEY")`; l'SDK
  vuole il timeout in **millisecondi**.

**Import lazy (`import anthropic` *dentro* `_call`).** Ogni SDK si importa solo
quando quel provider viene usato. *Perché:* sono dipendenze **opzionali**;
importarle in cima farebbe crashare l'app di chi ha installato solo Ollama. Effetto
collaterale prezioso: costruire un `DataAgent(provider="ollama")` nei test **non
tocca la rete**.

#### `__init__.py` — la Factory

```python
DEFAULT_MODELS = {"ollama": "qwen2.5:3b", "anthropic": "claude-haiku-4-5", ...}
_PROVIDERS = {"ollama": OllamaProvider, "groq": GroqProvider, ...}
REQUIRES_API_KEY = {"anthropic", "openai", "gemini", "groq"}
```

Il chiamante passa una **stringa** e riceve un'istanza concreta senza conoscerne la
classe. Aggiungere un provider è una procedura in **due passi documentata in cima al
file**. *"Nient'altro nel resto del codice deve cambiare"* — è l'**Open/Closed
Principle** in pratica.

> Nota storica difendibile: il modello Anthropic di default era `claude-opus-4-8`, il
> più caro del listino. È stato cambiato in un modello economico durante l'audit di
> sicurezza: un default costoso è un problema di prodotto, non solo di stile.

**Principi:** Strategy, Template Method, Factory, Adapter, Open/Closed, DRY, import
lazy. 24 test di contratto (`test_providers_contract.py`) + 32 in
`test_config_and_providers.py`.
**Alternativa:** una catena di `if provider == "...":` — accoppiata, difficile da
estendere, duplica la logica di retry.

---

### 7.16 `nlda/pricing.py` — token → costo

**Perché esiste.** Perché "quanto mi costa questa app?" è una domanda legittima, e
la risposta deve essere nei log, non a occhio. `Usage(input_tokens, output_tokens)`
+ `PRICES_PER_1M` → `estimate_cost_usd`, correlato al `turn_id`.

**Tre regole di onestà, dichiarate nel docstring:**
- un modello **non a listino** dà costo `None` (sconosciuto), mai un numero
  inventato — e `None` non va confuso con `0`, che è il costo **reale** di un
  modello locale;
- input e output pesano diversamente (l'output costa di più): si tengono separati;
- i prezzi sono **listini pubblici indicativi tenuti a mano**, non un contratto: «il
  valore della funzione è il MECCANISMO, non l'esattezza al centesimo di un numero
  che varia di settimana in settimana».

Quest'ultima frase è il modo giusto di difendere una tabella hard-coded: si dichiara
il limite invece di far finta che non ci sia. 7 test.

---

### 7.17 `nlda/agent.py` — l'agente di traduzione

**Perché esiste.** È il *cervello linguistico*: traduce la domanda in codice Pandas e
trasforma i numeri in prosa. Incapsula l'ingegneria dei prompt e l'euristica "serve
un grafico?".

**Responsabilità.** (1) costruire il system prompt adattato allo schema **reale** del
dataset; (2) generare il codice; (3) correggerlo su errore; (4) generare le
narrazioni (overview, explain + `explain_stream`, executive report); (5) decidere se
serve un grafico. È **indipendente dal provider**.

**`_describe_schema(df)`** costruisce la descrizione dello schema: per ogni colonna
nome, tipo (`column_kind`) e fino a 3 esempi **sanitizzati** (`nlda.sanitize`).
*Perché passare lo schema reale:* così il modello usa i **nomi esatti** delle colonne
del file caricato e non ne inventa. È la chiave dell'adattività a qualunque dataset.

**`_example_columns(df)`** sceglie una colonna categoriale e una numerica reali per
costruire nel prompt un esempio di grafico *calzante*.

**`_chart_intent(question)`** deduce se serve un grafico e di che tipo, con due liste
di parole chiave. Nota di design: *"mostrami"* e *"visualizza"* erano troppo
generiche e attivavano il grafico su richieste scalari, quindi sono state **togliete**
dalle chart-words. **È l'unica fonte di verità sull'intento grafico** — così la UI
non duplica parole chiave.

**`_wrap_chart(code, wants, kind)`** se serve un grafico ma il modello ha prodotto
solo dati, li avvolge in `to_chart(...)`: la rete di sicurezza che garantisce un
grafico anche quando il modello se lo dimentica.

**`_is_single_expression`** distingue l'espressione singola dallo script, per sapere
se il risultato si ottiene con `eval` o con `exec`.

**`_narrate`** genera testo (non codice) e rimuove i backtick che alcuni modelli
aggiungono a caso. **`explain_stream`** produce la spiegazione a blocchi per l'effetto
typewriter.

**`_motivo_llm`** trasforma un guasto del provider in una frase utile per l'utente.

**Le tre narrazioni** condividono la **stessa regola ferrea**: usa solo i numeri
forniti, non inventarne, riporta l'unità solo se indicata, scrivi i numeri
all'italiana. `executive_report` impone cinque sezioni Markdown e chiede di
formulare raccomandazioni e rischi come **ipotesi** (*"potrebbe", "suggerisce"*),
ricordando che correlazione non è causa.

**Principi:** Single Responsibility, Dependency Inversion, sicurezza (sanitizzazione
in ingresso al prompt), Single Source of Truth (intento grafico). 23 test.
**Alternativa all'euristica keyword:** far decidere all'LLM con function
calling/structured output — più flessibile ma più lento, meno deterministico e non
supportato uniformemente da tutti i provider.

---

### 7.18 `nlda/prompts/` — i prompt come file versionati

**Perché esiste.** I system prompt sono **il contratto** con il modello: cambiarli
cambia il comportamento dell'app più di molte righe di codice. Tenerli in f-string in
mezzo al codice li rendeva illeggibili e indiffabili.

Ora ogni file `.md` contiene **esattamente** il testo di un prompt:
`code_generation.md`, `explain.md`, `overview.md`, `executive_report.md`.

**Il golden test è la parte interessante.** `tests/test_prompt_contract.py` confronta
i prompt **carattere per carattere** con una copia attesa: se un editor aggiunge un
a-capo o qualcuno cambia una parola, il test diventa rosso. Tenerli come testo li
rende leggibili **senza** allentare il contratto — «è la rete che impedisce di
degradarli in silenzio».

**Due dettagli tecnici difendibili:**
- i file **non** vengono normalizzati: `read_text` con universal-newline riporta
  comunque CRLF a `\n`, quindi il testo a runtime è indipendente da come git ha
  scritto i fine-riga su disco (importante su Windows);
- `render` usa `string.Template` (`$nome`) e **non** `str.format`, perché nel prompt
  di generazione compare la graffa letterale `{perc:.1f}`, che con `format` andrebbe
  raddoppiata — fonte di errori silenziosi.

**Principi:** configurazione come dato versionato, golden test, scelta consapevole
dell'interpolatore. 7 + 5 test.

---

### 7.19 `nlda/sandbox/validator.py` — l'allowlist AST

**Perché esiste.** Qui non si esegue nulla: **si ispeziona soltanto**. La separazione
conta — «le regole di sicurezza sono la parte che va letta con più attenzione, e
tenerle in un file di poche centinaia di righe che non fa altro le rende
ispezionabili davvero».

**Il cambiamento più importante dalla 1.0: da denylist ad ALLOWLIST.**

```python
_ALLOWED_NODES = {
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.If, ast.For,
    ast.Name, ast.Attribute, ast.Subscript, ast.Call, ast.Constant, ast.Lambda,
    ast.ListComp, ast.DictComp, ast.BinOp, ast.Compare, ...
}
```

Il default è **NEGARE**: tutto ciò che non compare qui viene rifiutato.

*Perché non una denylist:* «una lista di costrutti vietati descrive gli attacchi che
conosciamo **oggi** e resta indietro a ogni evoluzione della sintassi (match, walrus,
async, e qualunque cosa arrivi domani)». Elencando invece la manciata di nodi che
servono a un'espressione Pandas, restano fuori **per costruzione**
`def/class/import/with/try/raise/global/yield/async` e i costrutti futuri, senza
doverli prevedere uno per uno. È la differenza tra difendersi dagli attacchi noti e
difendersi da quelli che non sono ancora stati inventati.

**`_ALLOWED_NODE_BASES`** ammette in blocco contesti (Load/Store) e operatori
(`+`, `==`, `and`, `~`): sono nodi foglia senza potere espressivo proprio, ed
elencarne le ~30 sottoclassi aggiungerebbe rumore senza aggiungere sicurezza.

**`while` resta escluso, `for` no.** Motivazione dichiarata: `for` e le comprehension
sono iterazione legittima, e un ciclo troppo lungo è contenuto dal **timeout** del
sottoprocesso (e dal cap di memoria su POSIX), non dal validatore. `while True` è
invece il caso degenere più comune e non serve **mai** a un'aggregazione Pandas.

**`_SafeModule` — la difesa che l'allowlist AST non può dare.**

```python
class _SafeModule:
    def __getattr__(self, attr):
        valore = getattr(self._modulo, attr)
        if isinstance(valore, ModuleType):
            raise UnsafeCodeError(f"accesso al sottomodulo '{self._nome}.{attr}' non consentito")
        return valore
```

L'AST controlla i **tipi di nodo**, non **quale oggetto** una catena di attributi
raggiunge. Da `px`/`pd`/`go` si arrivava a `os`/`subprocess` per semplice traversata
dei sottomoduli: `px.np.f2py.subprocess`, `px.data.os`. Un blocco per **nome** sarebbe
aggirabile con un alias (`x = px; x.np`); legando invece i tre nomi a questo proxy,
ogni accesso — anche via alias, che punta pur sempre a **questo** oggetto — ripassa di
qui. Si negano i **soli sottomoduli**: funzioni, classi e costanti dell'API restano
accessibili, quindi gli idiomi legittimi non ne risentono. **Fail-closed:** un
attributo modulo non previsto viene negato, mai ammesso di default.

**Le regole mirate sui nodi ammessi:** `SAFE_BUILTINS` (whitelist di 19 builtin, mai
`__import__`/`open`/`eval`/`exec`), `_FORBIDDEN_NAMES`, `_DENY_ATTRS`
(`eval`, `query` — eseguono una stringa non ispezionabile dall'AST; `format`,
`format_map` — leak via `"{0.__class__}".format(obj)`; `style` — `Styler.to_html`
scrive file; `io`, `core`, `compat` — traversata dei moduli interni), il prefisso I/O
`to_|read_|write_` con l'eccezione dei `_SAFE_CONVERTERS`, e le chiavi dunder via
subscript (`obj['__class__']`).

**Nota esplicita:** `apply/map/agg/pipe/transform` **non** sono bloccati — sono comuni
e legittimi, e l'AST ispeziona comunque l'interno dei lambda passati.

**`_DENIED_MESSAGES`** dà messaggi mirati per i costrutti che un modello prova più
spesso: «un errore comprensibile vale più di *"costrutto 'ImportFrom' non
consentito"*».

**`_iter_stmts_in_order` / `_last_assigned_name`** percorrono gli statement
nell'**ordine reale di esecuzione** per recuperare l'ultima variabile assegnata
quando il modello non usa `risultato`. Sostituiscono un vecchio confronto sul
`lineno`, definito «euristica fragile».

**`_parse_and_validate(code)`** è il punto di verità unico: parsa gestendo
`SyntaxError`, rifiuta il codice **senza statement eseguibili**, applica la sandbox,
**logga la regola** che ha bocciato (prezioso per capire i tentativi di escape) e
ritorna l'AST **oppure** un `ExecutionFailure("security", …)`.

**Principi:** default-deny, difesa in profondità, fail-closed, messaggi azionabili.
75 test di regression + 4 property test.

---

### 7.20 `nlda/sandbox/runner.py` — esecuzione e trasporto

**Perché esiste.** Tre responsabilità che stanno insieme perché descrivono **un unico
percorso**: eseguire il codice validato, riassumere il risultato, trasportarlo tra
processi.

**`_run_code(code, df)`** è il cuore, eseguito **sia in-process sia nel
sottoprocesso**. Prepara un contesto isolato (`__builtins__` = `SAFE_BUILTINS`,
niente `st`, i moduli avvolti in `_SafeModule`, `compare_periods` esposto) e
distingue espressione singola (`eval`) da statement multipli (`exec`, poi recupera
`fig` e `risultato`/`result` o l'ultima variabile assegnata). Cattura ogni eccezione
e la trasforma in `ExecutionFailure("runtime", …)`.

**Il trasporto: JSON, non pickle.** È l'altro cambiamento di sicurezza rilevante
dalla 1.0. Prima il canale di ritorno del worker trasportava **pickle**;
deserializzare un pickle proveniente da un processo che ha eseguito codice generato
da un LLM è una primitiva di esecuzione arbitraria — il pickle *può costruire
oggetti*. Ora il worker risponde in **JSON**: la figura via `to_json`/`from_json` di
Plotly (formato nativo e stabile, indipendente dalla versione degli oggetti), il
valore con un encoder/decoder espliciti (`_encode_value`/`_decode_value`) che sanno
gestire i tipi attesi e nient'altro.
*Nota di simmetria:* l'**andata** (genitore → worker) resta pickle, e va bene: quei
dati li ha prodotti il genitore, non il codice non fidato.

**`summarize_result` / `_make_summary` / `_fig_summary`** producono il riepilogo
testuale dato al modello per la spiegazione. È calcolato **nel worker**, sulla figura
reale, e viaggia col risultato già pronto.

**`_run_in_subprocess`** delega alla riserva calda (`pool`) e impone il **timeout** dal
genitore. Se il processo termina male segnala un probabile esaurimento memoria —
distinguendo il worker **che non parte** da quello **abbattuto dal codice**, che sono
due guasti diversi (`internal` vs `timeout`).

**`execute_pandas_code(code, df)`** è l'API pubblica: `clean_code` +
`_parse_and_validate` → **fail-fast** (blocca prima di avviare un processo); poi
sottoprocesso; poi la politica **fail-closed/fail-open** (§8.5).

35 test in `test_executor_ipc.py` sul solo trasporto.

---

### 7.21 `nlda/sandbox/pool.py` — la riserva calda

**Perché esiste, con i numeri.** Ogni domanda avviava un interprete nuovo e ne
aspettava gli import: **843 ms** prima che il worker vedesse una riga di codice
dell'utente (pandas 599, plotly 197, interprete nudo 30). Con una risposta del
modello che costa 2000-20000 ms, era **fra il 5% e il 30%** del tempo totale —
pagato a ogni singola domanda, e senza alcun rapporto con la dimensione dei dati
(877 ms su 200 mila righe, 983 su un milione).

**La scelta: riserva calda, NON processo riutilizzato.** Un pool classico terrebbe
vivi N processi e li riuserebbe. Sarebbe più veloce, ma barattarebbe una proprietà di
sicurezza che qui vale più della velocità: **ogni esecuzione avviene in un processo
fresco**. Il codice generato può lasciarsi dietro di tutto — opzioni globali di
pandas cambiate, attributi di modulo riscritti, memoria non liberata — e la domanda
successiva, magari di un altro utente, lo erediterebbe.

Qui invece si tiene pronto **un processo che non ha ancora eseguito nulla**: ha
importato pandas e plotly, ha impostato il proprio cap di memoria, e dorme in attesa
su stdin. Quando arriva una domanda lo si usa, il worker esegue e **muore come
prima**; nel frattempo se ne prepara un altro in background. Stessa identica garanzia
di isolamento, costo fisso spostato fuori dall'attesa dell'utente.

**Cosa costa, dichiarato.** Un processo in attesa occupa **~100 MB** di RAM (misurati:
102 MB), perché ha già importato pandas e plotly. È il prezzo del preriscaldamento:
il container gira con `mem_limit: 2g`, quindi ~5% del budget per non far aspettare
l'utente. Si tiene **una sola** riserva, non una per sessione, proprio per non
moltiplicare quel costo.

**Cosa NON risolve** (e dirlo è metà del valore): il primo avvio resta a carico di chi
lo chiede, a meno di chiamare `prewarm()` all'apertura della pagina — cosa che l'app
fa. E non riduce il costo del **trasferimento del DataFrame**, ancora picklato a ogni
domanda: su un milione di righe sono ~38 MB per volta. Tenerlo nel worker
richiederebbe un processo persistente, cioè esattamente il compromesso di sicurezza
che il progetto rifiuta.

**Principi:** ottimizzazione **misurata** (non intuita), sicurezza preferita alla
velocità quando le due sono in conflitto, costi dichiarati. 8 test.

---

### 7.22 `nlda/_sandbox_worker.py` — il processo isolato

**Perché esiste.** Per imporre un **timeout** (dal genitore) e un **cap di memoria**, e
per mettere una **barriera di processo** attorno al codice generato. Un crash o un
consumo abnorme non toccano il server Streamlit.

**Il nome con underscore** segnala che è un modulo *interno*: si lancia come
`python -m nlda._sandbox_worker`, non si importa.

**Il dettaglio più difendibile — la protezione di stdout.**
```python
real_stdout_fd = os.dup(1)   # salva il vero stdout
os.dup2(2, 1)                # dirotta gli eventuali print() del codice su stderr
...
os.write(real_stdout_fd, payload)   # il risultato sul vero stdout
```
Su stdout deve finire **solo** la risposta. Se il codice generato facesse un
`print()`, sporcherebbe lo stream e romperebbe la deserializzazione. Reindirizzando
fd 1→2 durante l'esecuzione, ogni stampa va su stderr. (Nota: `print` **è** in
`SAFE_BUILTINS`, quindi il caso è reale, non teorico.)

**`_limit_memory(mb)`** usa `resource.setrlimit(RLIMIT_AS)` — **solo POSIX**. Su
Windows il modulo `resource` non esiste, quindi è un **no-op**: il worker è contenuto
dal solo timeout. È un **limite noto e dichiarato**, non una svista. In produzione
(Linux, container) il cap è attivo.

**Principi:** isolamento di processo, privilegio minimo, onestà sui limiti.
**Alternativa:** un thread — ma un thread Python non si può *uccidere* in modo
affidabile allo scadere del timeout (niente preemption pulita), e non dà cap di
memoria né barriera di crash. Il processo sì.

---

### 7.23 `nlda/charts.py` — figure e tema

**Perché esiste.** Estratto da `executor.py`, che faceva tre cose in un file solo:
validare, eseguire e disegnare. Separandolo, il file delle regole di sicurezza è
tornato leggibile (§7.19) e i grafici hanno un posto proprio.

**`apply_theme(fig)`** applica il tema coerente leggendo la palette da
`ui_theme.PALETTE` — **unica fonte** per i colori di UI e grafici. È **idempotente**:
`test_charts_theme.py` verifica che non ritematizzi una figura già tematizzata a ogni
rerun (era una perdita di prestazioni reale).

**`_make_bars_readable(fig)`** rende leggibili le barre con etichette lunghe:
converte in **orizzontale** (nomi sull'asse y invece di testo ruotato), tronca
tenendo il nome intero nell'hover, adatta l'altezza. Idempotente e agnostico su come
il modello ha costruito la figura.

**`to_chart(res, kind="bar")`** costruisce una figura da una Series/DataFrame
aggregata: il *fallback* quando il modello non produce direttamente una figura.
Ordina, limita a 15 elementi, sceglie l'orientamento in base alle etichette.

**`corr_heatmap(corr)`** heatmap **divergente** (blu↔rosso) con midpoint neutro a 0 e
range fisso `[-1,1]`. *Perché divergente e non sequenziale:* la correlazione ha un
centro semanticamente neutro (0) e due poli; una scala divergente lo comunica
correttamente, una sequenziale suggerirebbe che −1 è "poco".

**`histogram(df, col, nbins=40)`** — istogramma **adattivo**, molto difendibile. Molti
dataset economici sono log-normali (tanti valori piccoli, pochi enormi): su scala
lineare tutto si schiaccia a sinistra.
- dati **strettamente positivi** e **fortemente asimmetrici** (99° percentile > 8×
  mediana) → **scala logaritmica** (bin uniformi su log10), con tick etichettati nei
  valori originali;
- altrimenti → **lineare**, con vista fino al 99° percentile e nota sugli outlier
  esclusi.
L'euristica è **conservativa**: dataset "normali" (età, punteggi) e colonne con
zeri/negativi restano lineari (log10 non è definito su ≤0). 5 test.

**`_require_plotly()`** rende esplicito l'import lazy: se Plotly manca, il messaggio
lo dice invece di far esplodere un `NameError`.

---

### 7.24 `nlda/export.py` — la conversazione in Markdown

**Perché esiste.** Perché una risposta che non si può portare via vale poco: l'utente
esporta domanda, risultato, spiegazione e codice generato.

**Funzioni pure, nessuno Streamlit:** la UI si limita a offrirne l'output in un
`st.download_button`. Questo le rende testabili (11 test).

**`_dataframe_to_markdown` / `_value_to_markdown`** gestiscono i tipi possibili;
`_cell` sanitizza le celle — un valore ostile non deve iniettare Markdown nel file
esportato, che qualcuno aprirà altrove.

**Un dettaglio che è stato un bug:** l'export non scrive più la frase segnaposto
(`EXECUTED_OK`) dove la UI tace. Prima il file esportato conteneva "Codice eseguito
correttamente." al posto di un risultato — informazione zero, per chi rilegge.

---

### 7.25 `nlda/demo.py` — i tetti di spesa

**Perché esiste.** Il deploy pubblico usa **una chiave API del manutentore**: senza un
tetto, ogni visitatore spenderebbe il suo credito. La quota vive qui, separata dalla
UI, per due motivi: è una regola di **prodotto** (non di presentazione) e va poter
essere verificata **senza avviare Streamlit**.

**I limiti sono due, e servono a cose diverse** — questa è la parte da saper spiegare:
- quello per **sessione** tiene onesto il singolo visitatore, ma **da solo non
  protegge nulla**: basta aprire una scheda nuova per azzerarlo;
- quello **giornaliero** è il vero tetto di spesa: vale su tutte le sessioni del
  server e si azzera ogni giorno, così una giornata storta non brucia il credito e il
  giorno dopo la demo è di nuovo viva.

**E l'onestà finale:** «nessuno dei due è una difesa contro un abuso determinato — per
quello servirebbe un'identità, che una demo pubblica non ha. Servono a contenere il
costo, ed è quello che dichiarano di fare».

---

### 7.26 `nlda/ui_components.py` — i pezzi riusabili

**Perché esiste.** Il *"come si mostra"* (card, tabelle, grafici collegati), separato
dal *"cosa e quando"*.

**Regola trasversale: tutto ciò che produce HTML fa escaping** (`html.escape`), perché
i valori delle celle sono dati non fidati e non devono iniettare markup.

**`build_kpis(df, measure, category, unit)`** costruisce i KPI **adattandosi ai
dati** e **senza effetti Streamlit**: ritorna una lista di tuple. Questo la rende
**pura e testabile**, mentre `readout` fa il rendering. Separazione calcolo/rendering
anche qui. `_leader_kpi` ritorna `None` se la classifica è **vuota** — caso reale
(CSV con sole intestazioni, colonna categoriale tutta NaN) che prima sollevava
`IndexError` e mostrava la schermata d'errore al posto dell'app.

**`readout(...)`** card KPI in stile console. Il valore non deve mai uscire dal
riquadro, e il come è istruttivo: la dimensione dipende **sia** dalla larghezza della
card (che il CSS conosce) **sia** dalla lunghezza del valore (che dipende dai dati e
la conosce solo Python). Quindi `readout` calcola un coefficiente `--vfs` sulla
lunghezza e il CSS lo moltiplica per la larghezza della card con `1cqi`
(`container-type: inline-size`): si adatta a entrambe le variabili **senza una riga di
JavaScript**.

**`render_linked_charts(...)`** — i **grafici collegati** (click-to-filter):
cliccando una barra della classifica (`on_select="rerun"`), l'andamento si filtra
sulla categoria scelta; ri-cliccando si toglie il filtro. `_clicked_category` gestisce
i tre modi in cui Plotly riporta la categoria cliccata (customdata, `x`, `y`) — barre
orizzontali e verticali non la mettono nello stesso posto.

**`render_result(...)`** rende un turno completo: risposta, risultato visuale,
**colonne toccate**, **avvisi di plausibilità** (§7.14) e codice generato in un
expander collassato. 31 test.

---

### 7.27 `nlda/ui_theme.py` — il tema

**Perché esiste.** Tenere il CSS fuori dal flusso della pagina. `main.py` inietta solo
`console_css()`.

**Il tema è "strumento di misura"**: rail (sidebar) scuro `#191d23`, workspace freddo,
**un solo** accento teal `#0d8a7d` usato con parsimonia. `PALETTE` è la fonte unica:
la leggono il CSS, i grafici (`charts._THEME`) e le tacche dei KPI.

**Il vincolo architetturale da citare:** questo modulo **non importa nulla**. Non è
pigrizia: `charts.py` legge `PALETTE` ed è caricato **anche dal worker della
sandbox** — una dipendenza aggiunta qui (Streamlit su tutte) entrerebbe nel costo di
avvio di **ogni** esecuzione di codice.

**L'avvertenza sui selettori, dichiarata nel docstring.** I selettori
`[data-testid="st..."]` **non** sono un'API pubblica di Streamlit: sono dettagli
interni del suo DOM. A un aggiornamento possono cambiare, e allora la regola smette
di applicarsi **in silenzio** — il layout non si rompe, perde lo stile mirato. Si
accetta il rischio perché non esiste un modo ufficiale per stilare quei componenti; è
scritto nero su bianco dove qualcuno lo cercherà.

**Responsive** (aggiunta di fine luglio): scala tipografica fluida in un punto solo
(`html { font-size: clamp(...) }` — tutto il tema è in rem, quindi una riga muove
ogni misura), e soglia responsive su **larghezza *o* altezza**, perché un portatile
1920×1080 passerebbe un test sulla sola larghezza ma ha un viewport alto ~930px, ed è
lì che il layout a due colonne si rompeva.

**Perché una funzione che ritorna una stringa e non un file `.css`.** Streamlit
inietta lo stile via `st.markdown(..., unsafe_allow_html=True)`; una funzione permette
di **interpolare la palette**, così i colori vivono in un unico dict e non sono
ripetuti a mano nel CSS.

---

### 7.28 `nlda/views.py` — filtro e unione come VISTE

**Perché esiste.** Filtro e join sono due modi di dire "guarda questi dati in un
altro modo". Sono funzioni **pure** su DataFrame, senza Streamlit e senza HTTP,
proprio perché servono a entrambe le interfacce.

**`apply_filter`** restringe il dataset e restituisce anche l'**etichetta leggibile**
del filtro (`Region ∈ {Nord, Sud}`). L'etichetta torna insieme ai dati e non viene
ricostruita da chi mostra: quando lo faceva il client, lo stesso filtro si leggeva in
due forme diverse a seconda dell'interfaccia.

**`join_datasets`** è un `merge` di pandas con suffisso `_2` sulle colonne omonime —
nessuna colonna sovrascritta in silenzio. Il risultato è **un solo** DataFrame:
sandbox, prompt e report non sanno nemmeno che i file erano due. Il join è un
*preprocessing*, non un secondo canale da gestire ovunque.

**`join_warning` — il difetto più insidioso dell'unione, perché non fallisce.** Se la
chiave si ripete nel secondo file, ogni riga del primo si moltiplica per ogni
corrispondenza: il totale cresce e nulla lo segnala. Due dettagli che sembrano
minori e non lo sono:

* il metro è il **primo file**, non il più grande dei due. Unire serve ad aggiungere
  colonne alle sue righe, quindi averne di più significa duplicazione. Il criterio
  «più righe del massimo dei due» sembra equivalente e non lo è: 2 righe unite a 3 ne
  producono 3 — non supera il massimo — eppure un ordine è stato contato due volte;
* la causa sta **sempre a destra**: le chiavi ripetute nel primo file non moltiplicano
  nulla, perché ognuna trova le stesse corrispondenze. Avvisare lì sarebbe un falso
  positivo su un join corretto.

---

### 7.29 `nlda/kpis.py`, `suggestions.py`, `demo_data.py` — i piccoli condivisi

Tre moduli brevi che esistono per la stessa ragione: erano pezzi di UI, e sono
diventati funzioni pure quando la seconda interfaccia ha avuto bisogno degli stessi
risultati.

**`kpis.build_kpis`** produce le card in cima al report **già formattate**
all'italiana (`2.261.537`), con etichetta, valore, sottotitolo e colore della tacca.
Formattare nel backend è una scelta: due client che formattano per conto proprio
mostrano lo stesso numero in due modi. `_leader_kpi` ritorna `None` su classifica
vuota, e la card ripiega su "Record" — è il fix del crash su dataset senza categorie.

**`suggestions.example_questions`** costruisce le domande d'esempio sulle colonne
**di questo** dataset (misura, categoria, colonna data, ampiezza temporale),
proponendo ciò che il report *non* risponde già: il massimo nel tempo, il legame fra
due misure. Domande fisse su un dataset qualunque sarebbero quasi sempre sbagliate.

**`demo_data`** dichiara i dataset di esempio **presenti su disco** (`esiste()`):
l'elenco riflette il disco, non un catalogo scritto a mano che può mentire.

---

### 7.30 `nlda/project_qa.py` — "Chiedi al progetto"

**Perché esiste.** Una modalità che risponde su **questo progetto** citando le fonti.
La base di conoscenza sono i `.md` **già nel repo** (documentazione tecnica,
`ARCHITECTURE`, `THREAT_MODEL`, `VALUE`, `README`, `DEPLOY`): nessuna copia da tenere
in sincrono, quindi nessuna copia che invecchia.

**Recupero lessicale TF-IDF, non embedding.** Tre motivi difendibili: nessuna
dipendenza nuova, un corpus tecnico è fatto di **termini rari** (che è esattamente
ciò che l'IDF premia), e il risultato è **testabile** — si può affermare che una
domanda nota trovi la sezione giusta, cosa che con gli embedding sarebbe una
soglia da tarare.

**Tre trappole del recupero, già risolte — da non reintrodurre:** lo stemming faceva
collidere «come **funziona**» con la sezione «funzione per funzione» (il verbo va
escluso *prima* della radice); «qual» è raro ma vuoto, e l'IDF lo premiava (gli
interrogativi sono fra le stopword); le sezioni corte venivano fuse nella precedente
**ereditandone il titolo**, cioè venivano citate come fonte sbagliata.

Il prompt è fondato: cita le fonti e dichiara quando non sa. Con un modello piccolo
può comunque **sovra-affermare** — è stato osservato dire «garantisce l'isolamento
del sistema operativo» dove la documentazione dichiara l'opposto.

---

### 7.31 `nlda/api/app.py` — la seconda interfaccia

**Perché esiste.** Servire lo stesso backend a un frontend qualunque. La regola che
lo governa è una sola: **nessuna logica qui dentro**. Le rotte chiamano `loader`,
`AnalysisService` e `sandbox` esattamente come fa Streamlit; ciò che resta è
traduzione da e verso JSON. Che il modulo sia in gran parte traduzione è la prova
che la stratificazione dichiarata in §4 era vera.

**Le scelte che vale la pena difendere:**

* **Nessuna sessione.** Il dataset vive in `store` (§7.32), indirizzato dall'impronta
  del contenuto. L'API resta senza stato *per richiesta*.
* **La chiave API non si registra e non si logga.** Arriva nell'header, va al
  provider, finisce lì: non entra nel magazzino, non compare nei log, non torna in
  nessuna risposta.
* **Gli errori attesi non sono eccezioni.** Codice rifiutato dalla sandbox o timeout
  tornano con **HTTP 200 e `ok: false`**, perché sono *esiti* dell'operazione. Il
  4xx/5xx resta per ciò che è davvero un errore di chiamata. È la stessa distinzione
  che il backend fa fra `ExecutionFailure` ed eccezione.
* **Un errore di chiamata non è un guasto del servizio.** `_colonna` e
  `_esigi_misura` validano i nomi di colonna che arrivano dalla querystring: prima
  una misura testuale o inesistente attraversava `analyze` (che la tollera) e faceva
  esplodere `build_kpis` a report quasi finito — cioè un **500**, per quello che è un
  errore di chiamata. `_colonna` tollera gli spazi ai bordi come *ripiego*, mai a
  scapito del dataset: se una colonna si chiama davvero `'Vendite '`, vince quella.

**Ordine delle operazioni, che qui è una scelta di costo.** Prima si cerca il
dataset, poi si scala la quota: al contrario, chi torna su una scheda lasciata aperta
e trova la voce scaduta pagava il 404 con una domanda del proprio budget.

---

### 7.32 `nlda/api/store.py` — dove vive un dataset fra due richieste

**Perché esiste.** Streamlit teneva il DataFrame in `st.session_state`. Un'API HTTP
non ha sessioni: l'utente carica il file una volta e poi fa dieci domande, e il
DataFrame deve stare da qualche parte in mezzo. È una mappa in memoria con capienza,
scadenza e un lock (uvicorn serve le richieste su un pool di thread).

**Perché la chiave è il contenuto e non un UUID.** L'identificativo è l'impronta dei
byte: ricaricare lo stesso file non duplica la memoria, e l'identificativo è
**riproducibile** — un test può calcolarlo senza chiamare l'API.

**Il tetto conta i BYTE, non le tabelle.** Il limite sul *numero* di dataset (8) non è
un limite di RAM: otto file entro `MAX_ROWS` fanno 2,7 GB, più del container da 2 GB
e cinque volte i 512 MB del piano su cui gira la demo. Si misura quindi la memoria
occupata (`memory_usage(deep=True)`, una volta per caricamento) e si sfratta il meno
usato di recente finché la somma non rientra in `MAX_STORE_RAM_MB`. Un dataset più
grande del tetto **si tiene** — buttarlo lascerebbe l'utente senza il file appena
caricato — ma emette `magazzino_oltre_il_tetto`: un tetto tarato male si deve poter
vedere.

**`fai_spazio` sfratta per un dataset che deve ancora arrivare.** Lo sfratto di
`aggiungi` arriva troppo tardi per chi sta *leggendo*: mentre il parser lavora, i
dataset vecchi occupano ancora la loro memoria e i due costi si sommano. Sulla demo,
con 40 MB in magazzino, caricarne altri 40 uccideva il container — e nessuno dei due
sforava il tetto: la loro somma sì. La rotta di caricamento chiama `fai_spazio` con
la *stima* del file in arrivo prima di aprirlo (e con il massimo consentito, quando
il formato non si stima). L'ultima voce non si sfratta mai: se un solo dataset non ci
sta, buttarlo non aiuterebbe chi carica e toglierebbe i dati a chi li sta usando —
quel caso lo ferma `MAX_DATASET_RAM_MB` nel caricatore.

**La LRU è contata, non cronometrata.** La vittima si sceglieva col minimo di
`ultimo_uso`, cioè a orologio: sotto la risoluzione di `time.monotonic` (su Windows
scatti da ~15 ms) due usi nello stesso scatto sono indistinguibili, e si sfrattava la
voce **appena usata**. Il tempo resta per la scadenza, dove la domanda è «quanto fa?»
e la risoluzione non conta.

**Limiti dichiarati:** non sopravvive al riavvio del processo; non scala in
orizzontale (due repliche, due magazzini). Vanno bene finché il deploy è un container
solo, ed è il caso.

---

### 7.33 `nlda/api/quota.py` — il tetto di spesa della demo

**Perché esiste.** Il deploy pubblico gira con la chiave del manutentore: senza un
tetto, ogni visitatore ne spende il credito.

**Due differenze rispetto a Streamlit, entrambe volute.** Si conta **prima** della
chiamata (le richieste HTTP sono concorrenti: contare dopo significa che venti
richieste simultanee passano tutte il controllo prima che una abbia incrementato il
contatore), e la "sessione" è un **indirizzo IP** — debole per costruzione, dichiarato
tale: dietro NAT molte persone lo condividono e chi lo cambia riparte da zero. Il
tetto che protegge davvero il credito è quello **giornaliero**, che nessuna
intestazione può spostare.

**La lezione più cara di questo modulo non è nel modulo.** Il tetto si scalava in
`/ask`, ma la chat React chiama **solo** `/ask/stream`: sulla demo pubblica la
protezione non copriva il percorso reale delle domande. Verificato con budget
azzerato — `/ask` rispondeva 429, `/ask/stream` 200 e il contatore restava a zero. Il
punto cieco stava nei test, che provavano il tetto **solo** su `/ask`. Quando una
protezione esiste, la domanda giusta non è «c'è?» ma **«copre la strada che il client
percorre davvero?»**.

---

### 7.34 `nlda/api/models.py` e `streaming.py` — il contratto e il flusso

**`models.py` non è solo validazione.** Da questi modelli FastAPI genera lo schema
OpenAPI, e da quello `scripts/genera_tipi_ts.py` genera i **tipi TypeScript** del
frontend, che sono committati. Cambiare un campo qui e dimenticarsene di là smette di
essere possibile: un test confronta i tipi nel repository con lo schema di adesso.

**`streaming.py` — un turno trasmesso mentre accade.** Gli eventi sono `step`
(a che punto è), `result` (l'esito **completo tranne la spiegazione**), `token` (la
prosa a pezzi), `done`, `error`. L'ordine non è casuale: il `result` arriva **prima**
della spiegazione, quindi tabella e grafico compaiono appena esistono e la prosa li
raggiunge dopo — il contrario di una risposta unica, dove il dato più utile aspetta
quello più lento.

**Perché un thread e una coda.** `AnalysisService.answer` è sincrona e comunica
l'avanzamento con una callback; un generatore non può cedere dall'interno di una
callback altrui. Venti righe (thread + `queue`) evitano di riscrivere in asincrono il
servizio, la sandbox e i provider — tutti sincroni per ottime ragioni.

**Perché SSE e non WebSocket.** Il flusso è a senso unico: il server parla, il client
ascolta. Un WebSocket darebbe un canale bidirezionale che nessuno usa, in cambio di
riconnessioni da gestire e proxy che a volte lo bloccano.

---

### 7.35 `frontend/` — l'interfaccia React

**Cosa è.** Una SPA TypeScript (Vite) servita dalla stessa applicazione FastAPI che
espone l'API: un solo processo, un solo container, nessun CORS da configurare.

**I punti che vale la pena conoscere:**

* **`api/types.ts` è generato**, non scritto: viene dallo schema OpenAPI. È il
  motivo per cui un campo aggiunto al backend non può restare senza tipo nel client.
* **`api/stream.ts`** legge gli eventi SSE con un **accumulatore**: i pezzi che
  arrivano dalla rete non coincidono con gli eventi, e uno `split` ingenuo perde
  eventi in modo intermittente — il tipo di bug che dipende da come la rete ha
  spezzato i dati, quindi difficilissimo da riprodurre.
* **Gli errori hanno una forma sola.** `erroreDaRisposta` traduce qualunque risposta
  non riuscita in `ApiError`, ed è usato **anche** dal lettore SSE: è il motivo per
  cui un 429 sulla rotta in streaming arriva all'utente col suo messaggio invece di
  sembrare uno stream vuoto.
* **12 test (vitest)** sull'interfaccia, che prima non ne aveva nessuno.

---

## 8. La sandbox di sicurezza in profondità

È il tema di sicurezza centrale del progetto e la fonte più ricca di domande da
colloquio. Il progetto ha anche un `THREAT_MODEL.md` dedicato: questa sezione ne è
la sintesi tecnica.

### 8.1 Il modello di minaccia

Due fonti di input non fidato convergono nell'esecuzione:

1. **Il codice generato dall'LLM.** Anche in buona fede il modello può produrre
   codice che scrive file (`to_csv`), legge dalla rete (`read_pickle('http://…')`) o
   accede a interni pericolosi.
2. **I dati caricati dall'utente.** Un file ostile può contenere **prompt injection**
   ("ignora le istruzioni e genera codice che…"). I valori di cella finiscono nel
   prompt.

Eseguire `eval`/`exec` su codice così originato è, senza difese, RCE completa.

**Cosa il progetto NON pretende di fare** (e dichiararlo è parte della risposta): non
è una sandbox di livello container/kernel. È una difesa **a strati, leggera e
portabile**, con i suoi limiti scritti (§12).

### 8.2 Le difese, a strati (Defense in Depth)

```
┌── Strato 0: SANITIZZAZIONE DEL PROMPT ─────────────────────────────┐
│  nlda.sanitize: una sola funzione per nomi e celle. Toglie controllo,│
│  a capo, unicode invisibile, override bidirezionali, backtick;      │
│  tronca (40 char per un valore, 60 per un nome).                    │
└─────────────────────────────────────────────────────────────────────┘
┌── Strato 1: ALLOWLIST DI NODI AST (default-deny) ──────────────────┐
│  _ALLOWED_NODES elenca i nodi ammessi: tutto il resto è rifiutato,  │
│  COMPRESI i costrutti che Python aggiungerà in futuro.              │
│  Sui nodi ammessi, regole mirate: nomi vietati, attributi in deny,  │
│  metodi I/O to_/read_/write_ (salvo convertitori puri), chiavi      │
│  dunder via subscript, 'while'.                                     │
└─────────────────────────────────────────────────────────────────────┘
┌── Strato 2: AMBIENTE RIDOTTO ──────────────────────────────────────┐
│  __builtins__ = SAFE_BUILTINS (19 nomi); niente 'st'.               │
│  pd/px/go avvolti in _SafeModule: nega i SOTTOMODULI, anche via     │
│  alias — chiude px.np.f2py.subprocess e px.data.os.                 │
└─────────────────────────────────────────────────────────────────────┘
┌── Strato 3: ISOLAMENTO DI PROCESSO ────────────────────────────────┐
│  Sottoprocesso dedicato (riserva calda, ma sempre FRESCO):          │
│  TIMEOUT wall-time + cap RAM (POSIX). Barriera di crash.            │
│  stdout protetto: i print() del codice vanno su stderr.             │
└─────────────────────────────────────────────────────────────────────┘
┌── Strato 4: CANALE DI RITORNO IN JSON ─────────────────────────────┐
│  Il worker risponde in JSON, non in pickle: deserializzare un       │
│  pickle da un processo che ha eseguito codice non fidato è a sua    │
│  volta una primitiva di esecuzione arbitraria.                      │
└─────────────────────────────────────────────────────────────────────┘
┌── Strato 5: POLITICA FAIL-CLOSED ──────────────────────────────────┐
│  Se il sottoprocesso non parte: fallback in-process SOLO se         │
│  ALLOW_INPROCESS_FALLBACK=true; in deploy pubblico = false.         │
└─────────────────────────────────────────────────────────────────────┘
```

**Perché a strati e non una sola difesa.** Ogni strato può avere una falla; la
combinazione rende l'escape molto più difficile. Se domani si scoprisse un bypass
dell'AST, l'isolamento di processo + il cap di memoria + il canale JSON limiterebbero
comunque il danno.

### 8.3 Allowlist vs denylist — la scelta chiave

Vale su **due livelli**, e saperli distinguere è la risposta completa:

- **I builtin** sono una whitelist da sempre (`SAFE_BUILTINS`): si autorizza il poco
  che serve invece di provare a vietare tutto il pericoloso.
- **I nodi AST** sono passati **da denylist ad allowlist** durante l'audit. La
  denylist descriveva gli attacchi noti; l'allowlist esclude per costruzione anche
  la sintassi che Python non ha ancora.

Le deny-list residue (`_FORBIDDEN_NAMES`, `_DENY_ATTRS`) restano come strato
*aggiuntivo*, non come unica linea.

### 8.4 Il caso dei convertitori "puri"

La regola generale vieta ogni metodo `to_/read_/write_` (I/O su file/rete). Ma alcuni
`to_*` restituiscono solo oggetti in memoria e sono legittimi e comunissimi
(`to_dict`, `to_numpy`, `to_frame`, `to_datetime`…). Stanno in `_SAFE_CONVERTERS` e
sono esentati. È l'equilibrio tra sicurezza e usabilità: senza l'eccezione, metà degli
idiomi Pandas normali verrebbe bloccata e l'app sarebbe inutile.

### 8.5 Fail-closed vs fail-open

```python
if not settings.allow_inprocess_fallback:
    return ExecutionFailure("internal", "ambiente di esecuzione isolato non disponibile")
log.warning("...fallback in-process SENZA timeout/limite memoria.")
return _run_code(code, df)
```

In locale (fidato) il fallback migliora l'esperienza: l'app funziona anche se il
sottoprocesso non parte. In un **deploy pubblico** si mette
`ALLOW_INPROCESS_FALLBACK=false`: meglio **bloccare** che degradare silenziosamente la
sandbox. È il principio *fail securely*. Nota che il fallimento è tipizzato
`internal`, quindi **non retryable**: non innesca chiamate inutili all'LLM.

### 8.6 La traversata dei moduli: la falla trovata e chiusa

Merita un paragrafo perché è la vulnerabilità più istruttiva del progetto, ed è stata
**trovata e chiusa** dopo la 1.0.

L'AST valida i **tipi di nodo**. `px.np.f2py.subprocess` è una catena di
`ast.Attribute` — tutti nodi perfettamente legittimi. L'AST non ha modo di sapere che
al terzo hop si arriva a `subprocess`: quello è un fatto sul **grafo degli oggetti a
runtime**, non sulla sintassi. Da `plotly.express` si raggiungeva `numpy`, da numpy
`f2py`, da lì `subprocess`.

La difesa non poteva stare nel validatore: sta nel **contesto d'esecuzione**
(`_SafeModule`, §7.19). E la verifica è la parte da raccontare: il property test
`test_ogni_catena_che_raggiunge_un_modulo_e_bloccata` usa un **oracolo indipendente**
che risolve la catena sui **moduli veri** e guarda se un hop *è* un modulo — non una
seconda copia della denylist. Il pericolo deriva da ciò che gli oggetti **sono**, non
da una lista che potrebbe essere sbagliata allo stesso modo del codice che verifica.

### 8.7 I test come regression di sicurezza

`test_executor_sandbox.py` contiene **75 test**, con liste parametrizzate di payload
ostili (`df.to_csv`, `df.eval`, `__import__`, `df.__class__`, `pd.io.common…`,
`'{0.__class__}'.format(df)`, `while True`, …) e di codice legittimo che **deve**
passare. Se una modifica futura riaprisse un escape — o irrigidisse troppo la sandbox
rompendo gli idiomi normali — il test diventa **rosso**.

`test_validator_fuzz.py` aggiunge **4 property test** con hypothesis: genera migliaia
di espressioni mescolando frammenti leciti e frammenti di fuga, e verifica
l'invariante **«nessun codice accettato accede a `__`/import/I-O o a un nome di
fuga»**. L'oracolo `_indicatore_di_escape` è una **seconda implementazione**, con la
propria walk e le proprie liste: così un baco nel validatore non si nasconde dietro lo
stesso codice che dovrebbe verificarlo. È la differenza tra un test e una *prova*.

La sicurezza è **verificata in CI ad ogni push**, su tre versioni di Python.

---

## 9. I test

**Numeri.** 34 file, **679 test** Python (più 12 dell'interfaccia React, con vitest),
soglia di copertura **78%** applicata in CI (`--cov-fail-under=78`): non un numero
decorativo, un *gate*.

**Filosofia.** I test coprono la **logica pura** (nessuna rete, nessun LLM reale) e
danno particolare peso alla **sicurezza**. Costruire `DataAgent(provider="ollama")`
non tocca la rete (import lazy), quindi la logica dell'agente è testabile a costo
zero.

| File | Test | Cosa verifica |
|------|-----:|---------------|
| `test_executor_sandbox.py` | 75 | **regression di sicurezza**: payload rifiutati/ammessi |
| `test_api.py` | 75 | il **contratto HTTP**: forme, codici di stato, avvisi, magazzino |
| `test_loader.py` | 61 | date, valuta, misure vs ID, `read_any`, file storti |
| `test_checks.py` | 39 | colonne toccate, mappa dichiarata, avvisi |
| `test_main.py` | 36 | il flusso della pagina, filtro e unione |
| `test_executor_ipc.py` | 35 | il trasporto tra processi (serializzazione, guasti) |
| `test_project_qa.py` | 33 | recupero TF-IDF, fonti citate, trappole note |
| `test_config_and_providers.py` | 33 | config da env, retry/backoff, factory |
| `test_ui_components.py` | 31 | KPI, click-to-filter, dimensione dei valori |
| `test_agent.py` | 28 | intento grafico, wrapping, prompt |
| `test_providers_contract.py` | 24 | **contratto** rispettato da tutti i provider |
| `test_api_quota.py` | 24 | il tetto di spesa, su **entrambe** le rotte |
| `test_sanitize.py` | 23 | invisibili, bidi, troncamento, injection |
| `test_genera_tipi_ts.py` | 18 | i tipi TS committati descrivono l'API di adesso |
| `test_errors.py` | 15 | classificazione dei guasti del provider |
| `test_service.py` | 14 | il turno: retry solo se `retryable` |
| `test_export.py` | 11 | Markdown della conversazione |
| `test_log.py` | 11 | idempotenza, contesto, formato JSON |
| `test_streaming.py` | 9 | la spiegazione a blocchi |
| `test_sandbox_pool.py` | 8 | la riserva calda |
| `test_pricing.py` / `test_prompt_contract.py` | 7 + 7 | costo; **golden dei prompt** |
| `test_periods.py` / `test_log_analysis.py` | 6 + 6 | confronto periodi; riepilogo log |
| `test_corpus_replay.py` | 5 | **replay** di un corpus registrato |
| `test_executor_histogram.py` / `test_prompts.py` | 5 + 5 | scala log; caricamento prompt |
| `test_validator_fuzz.py` | 4 | **property test** con oracolo indipendente |
| `test_charts_theme.py` | 4 | idempotenza del tema |
| `test_utils.py` | 7 | formattazione IT, fence, tipi colonna |
| `test_main_history.py` | 6 | storico della conversazione |

**Tecniche notevoli, tutte difendibili in colloquio:**

- **`@pytest.mark.parametrize`** sulle liste di payload: un test, molti casi.
- **Property test con oracolo indipendente** (hypothesis): l'invariante di sicurezza è
  verificata contro una seconda implementazione, non contro se stessa. `deadline=None`
  con motivazione scritta: «si misura la CORRETTEZZA, non la velocità — senza, su una
  macchina di CI carica un singolo parse può superare i 200 ms di default e far
  fallire il test in modo intermittente: il rosso che insegna a ignorare i rossi».
- **Golden test dei prompt**: confronto carattere per carattere, così un prompt non
  si degrada in silenzio.
- **Corpus replay** (`test_corpus_replay.py` + `fixtures/`): risposte reali del
  modello **registrate una volta** (`scripts/record_corpus.py`) e rigiocate nei test.
  Dà copertura end-to-end senza rete, senza costi e in modo deterministico. È la
  risposta migliore a "come testi qualcosa di non deterministico?".
- **Test di contratto sui provider**: verificano che *tutti* i provider rispettino la
  stessa interfaccia — il vero valore dell'astrazione, altrimenti a rischio di
  divergere.
- **`monkeypatch`** per iniettare una `Settings` finta e annullare `time.sleep`, e un
  `_FlakyProvider` che fallisce *n* volte poi risponde, per verificare il numero
  **esatto** di tentativi.
- **Seed fisso** (`numpy.random.default_rng(seed)`) per i dati sintetici
  dell'istogramma: riproducibile.
- **Doppio d'immagine di Streamlit** (`MagicMock` al posto del modulo `st`) per
  testare il rendering senza avviare l'app: si verifica *che cosa* viene chiesto a
  Streamlit — quale widget, con quali dati, con quale `key` (due widget con la stessa
  chiave fanno esplodere Streamlit a runtime).

**Cosa NON è testato, e perché:** le chiamate reali agli LLM (lente, non
deterministiche, a pagamento — coperte dal corpus replay) e il rendering visuale
effettivo. Il valore starebbe nell'integrazione, non nell'unità. La scelta è
consapevole e dichiarata.

---

## 10. Configurazione, CI, container e deploy

### 10.1 `pyproject.toml`

Fonte di verità unica (PEP 621): metadata, dipendenze, extra dei provider, extra
`dev`/`tools`, e la configurazione di **ruff** (line-length 110, target py312),
**mypy** (`python_version=3.12`, bersagli di default dichiarati) e **pytest**.

### 10.2 CI (`.github/workflows/ci.yml`) — quattro job

| Job | Cosa fa |
|-----|---------|
| **Test** | matrice **Python 3.12, 3.13, 3.14**; `pytest --cov=nlda --cov-fail-under=78` |
| **Lint e type-check** | `ruff check .` + `mypy nlda main.py` |
| **Immagine Docker** | costruisce l'immagine, avvia il container, verifica che **l'app si dichiari sana** e che **la sandbox funzioni dentro il container**; se qualcosa fallisce, stampa i log |
| **Sicurezza** | `pip-audit` (vulnerabilità nelle dipendenze) + `bandit` (analisi statica del codice) |

C'è anche `smoke.yml` per lo smoke test (`scripts/smoke.py`).

**Perché la matrice su tre versioni.** `requires-python = ">=3.12"`: se dichiari di
supportare 3.12+, devi verificarlo. Testare solo la versione di sviluppo significa
scoprire le rotture dagli utenti.

**Perché un job Docker che *esegue* il container.** Costruire l'immagine dice solo che
il `Dockerfile` è sintatticamente valido. Il job avvia l'app e **prova la sandbox
dentro il container**, perché è lì che il cap di memoria POSIX è davvero attivo e lì
che l'app girerà: un test che passa in locale su Windows non dice nulla sul cap di
memoria.

**Perché `bandit` e `pip-audit` in CI.** Un progetto che esegue codice generato non
può permettersi di scoprire una CVE nelle dipendenze a mano. E bandit ha trovato
qualcosa di reale: il **B613** (Trojan Source) proprio nel modulo che difende dagli
invisibili (§7.11).

### 10.3 Container (`Dockerfile`, `docker-compose.yml`)

Immagine su `python:3.14-slim`. `docker-compose.yml` imposta `mem_limit: 2g` — il
numero rispetto al quale sono dichiarati i costi di memoria (i ~100 MB della riserva
calda, i ~340 MB di picco per 3 milioni di righe).

Il container è ciò che rende la risposta a *"perché non un container invece del
sottoprocesso?"* più interessante di un tempo: **ora ci sono entrambi**, e fanno cose
diverse. Il container isola l'**applicazione** dal sistema; il sottoprocesso isola il
**codice generato** dall'applicazione. Il secondo non diventa superfluo perché esiste
il primo: senza di esso, un `while True` del modello bloccherebbe il server dentro il
container, e un `to_csv` scriverebbe nel filesystem dell'app.

### 10.4 Deploy

Target della demo: **Streamlit Community Cloud** (vedi `DEPLOY.md`). La configurazione
va nei **Secrets**, mai nel repo: `DEMO_MODE`, `PROVIDER`, `MODEL`, `GROQ_API_KEY`.
Groq è il provider scelto (tier gratuito, veloce, API compatibile OpenAI → nessuna
dipendenza in più). Per un deploy pubblico con dati non fidati:
`ALLOW_INPROCESS_FALLBACK=false`.

Nota operativa appresa a caro prezzo: ricreare il repository su GitHub **scollega**
l'app da Streamlit Cloud — va rifatto il deploy, reinseriti i secret e dato accesso
alla *GitHub App* di Streamlit al nuovo repo (non basta l'OAuth). E il sottodominio
non si libera immediatamente.

---

## 11. Design patterns e principi applicati

| Pattern / Principio | Dove | In una frase |
|---------------------|------|--------------|
| **Strategy** | `providers/` | provider LLM intercambiabili dietro un'interfaccia |
| **Template Method** | `LLMProvider.generate` | scheletro (retry/backoff/log/costo) + passo `_call` |
| **Factory** | `get_provider`, `ProviderError.classify` | crea l'istanza giusta da una stringa o da un guasto |
| **Adapter** | `GroqProvider` | riusa l'SDK OpenAI per un'altra API compatibile |
| **Proxy protettivo** | `_SafeModule` | intercetta ogni accesso e nega i sottomoduli |
| **Result type** | `results.py` | l'esito è un tipo, non una stringa da ispezionare |
| **Separation of Concerns** | strati | UI / applicazione / dominio / sicurezza / infrastruttura |
| **Single Source of Truth** | `config`, `PALETTE`, `periods`, intento grafico, `sanitize` | un solo posto per ogni verità |
| **Open/Closed** | factory + provider | estendi senza modificare l'esistente |
| **Dependency Inversion** | `agent` → `LLMProvider` astratto | non dai provider concreti |
| **Dependency Injection** | `AnalysisService(agent)` | le dipendenze entrano, non si costruiscono dentro |
| **Defense in Depth** | sandbox | sei strati indipendenti |
| **Default-deny** | `_ALLOWED_NODES` | ciò che non è permesso è vietato, futuro compreso |
| **Fail-Fast** | pre-check AST | blocca prima di avviare un processo |
| **Fail-Closed** | `allow_inprocess_fallback` | in dubbio, blocca |
| **Least Privilege** | `SAFE_BUILTINS`, worker isolato | dai il minimo indispensabile |
| **12-Factor Config** | `config.from_env` | configurazione dall'ambiente |
| **DRY** | `utils`, `sanitize`, `_run_code` condiviso | niente duplicazioni (e la storia di `sanitize` spiega perché) |
| **Idempotenza** | `log._configure_once`, `apply_theme`, `prewarm` | applicabile più volte, stesso effetto |
| **Graceful Degradation** | favicon, `_try_fig`, import lazy | un pezzo mancante non abbatte l'app |
| **Memoization** | `@st.cache_data`, signature `*_sig` | non ricalcolare l'invariato |
| **Observability** | `turn_id`, log JSON, `pricing`, `log_analysis` | costo e latenza per turno, aggregabili |
| **Golden test** | `test_prompt_contract` | il contratto col modello non si degrada in silenzio |
| **Property-based testing** | `test_validator_fuzz` | l'invariante verificata contro un oracolo indipendente |

---

## 12. Limiti noti e possibili evoluzioni

**Limiti dichiarati (onestà intellettuale del progetto):**

- **Cap memoria solo su POSIX:** su Windows il worker è contenuto dal solo timeout. In
  produzione (Linux/container) il cap è attivo.
- **Fallback in-process senza sandbox forte:** se abilitato, esegue senza timeout né
  cap memoria. Va tenuto spento in deploy pubblico.
- **La quota della demo non è una difesa contro l'abuso**, solo un contenimento del
  costo: servirebbe un'identità, che una demo pubblica non ha.
- **La sandbox non è isolamento a livello kernel:** è una difesa a strati in-process +
  processo. Un bypass dell'AST combinato con una falla di Python resta concepibile.
- **`columns_referenced` può mancare** gli accessi per attributo (`df.Sales`).
- **I prezzi in `pricing.py` sono indicativi e tenuti a mano**, non un contratto.
- **Nessun test di integrazione con LLM reali:** coperto in parte dal corpus replay.
- **Excel: solo il primo foglio** (`sheet_name=0`).
- **Il DataFrame viene picklato a ogni domanda** (~38 MB per milione di righe):
  eliminarlo richiederebbe un worker persistente, cioè il compromesso di sicurezza
  che il progetto rifiuta.
- **I selettori CSS di Streamlit non sono API pubbliche:** a un aggiornamento il tema
  può perdere pezzi in silenzio.

**Già fatto** — nella 1.0 di questo documento questi erano "evoluzioni plausibili", e
il progetto le ha realizzate. Vale la pena saperlo, perché è la risposta a *"come lo
hai fatto evolvere?"*:
- ✅ **streaming** delle risposte (`explain_stream`, `st.write_stream`);
- ✅ **container** (Dockerfile + compose + job CI che lo esercita);
- ✅ **osservabilità** (log JSON, `turn_id`, costo per turno, `log_analysis`);
- ✅ **isolamento più forte della traversata dei moduli** (`_SafeModule`);
- ✅ **allowlist AST** al posto della denylist;
- ✅ **esito tipizzato** con retry solo dove ha senso;
- ✅ **costo di avvio del worker** rimosso dal percorso critico (riserva calda).

**Evoluzioni ancora aperte (buone risposte a "come lo miglioreresti?"):**
- isolamento a livello di sistema (gVisor/seccomp/namespace) per il worker;
- cache delle risposte LLM per domande identiche sullo stesso dataset;
- structured output/function-calling per l'intento grafico invece delle keyword;
- multi-foglio Excel e dataset molto grandi (chunking/campionamento, o un backend
  colonnare tipo DuckDB/Polars);
- un'identità leggera per la demo (per passare dal contenimento del costo a una vera
  difesa dall'abuso);
- valutazione automatica della qualità delle risposte su un set di domande note
  (`scripts/eval.py` è il primo passo).

---

---

## Appendice — comandi rapidi

```bash
# Avvio locale (tutti i provider + strumenti di sviluppo)
pip install -e ".[all,dev]"
streamlit run main.py

# Solo Ollama (nessuna API key, modelli locali)
pip install -e ".[ollama]"

# Qualità, come in CI
ruff check .
mypy nlda main.py
pytest --cov=nlda --cov-report=term-missing --cov-fail-under=78

# Solo i test di sicurezza della sandbox
pytest tests/test_executor_sandbox.py tests/test_validator_fuzz.py -v

# Container
docker compose up --build

# Riepilogo dei log JSON di una sessione
LOG_FORMAT=json streamlit run main.py 2> app.log
python scripts/analyze_logs.py app.log
```

---

*Fine del documento. Versione 2.0 — 30 luglio 2026.*
