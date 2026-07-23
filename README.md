# Natural Language Data Analyst

[![CI](https://github.com/raffaeleciccone-analyst/Natural-Language-Data-Analyst/actions/workflows/ci.yml/badge.svg)](https://github.com/raffaeleciccone-analyst/Natural-Language-Data-Analyst/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

*Analista di dati AI-powered che interroghi in linguaggio naturale.*

> ▶️ **Prova la demo: https://natural-language-data-analyst.streamlit.app/**

Interroga i tuoi dati in **linguaggio naturale**. Fai una domanda — ad esempio
*"Qual è il mese con più vendite?"* oppure *"Mostrami le vendite per regione"* —
e un modello LLM la traduce in codice Pandas, che l'app esegue in una sandbox.
Ottieni il **risultato**, un **grafico Plotly interattivo** e una **risposta
testuale** che interpreta i numeri.

Al caricamento di un dataset ricevi inoltre un **report iniziale automatico**:
KPI, statistiche, classifiche, andamento temporale, **correlazioni** tra le
misure e **insight automatici** — con una sintesi in linguaggio naturale e un
**report esecutivo** scaricabile. Tutti i numeri sono calcolati in Pandas; l'AI
si limita a raccontarli.

## Anteprima

![Report iniziale sui dati](docs/preview-report.svg)
![Risposta a una domanda](docs/preview-answer.svg)

*Sono anteprime del design, non screenshot dell'app in esecuzione: riproducono
palette e tipografia reali dell'interfaccia. Per provarla davvero c'è la
[demo](https://natural-language-data-analyst.streamlit.app/).*

## Funzionalità

- **Domande in linguaggio naturale** → codice Pandas generato dall'LLM →
  **grafici Plotly interattivi** e tabelle di dettaglio.
- **Multi-provider LLM**: Ollama (locale, senza API key), Groq, Anthropic,
  OpenAI, Gemini. Il provider e il modello si scelgono dalla barra laterale.
- **Formati supportati**: CSV, Excel (.xlsx/.xls) e JSON. L'app si adatta allo
  schema del file caricato: rileva tipi, date e colonne "misura" in automatico,
  e passa lo schema reale al modello a ogni domanda.
- **Report iniziale automatico** con KPI, statistiche, classifiche e andamento
  temporale, più una panoramica testuale generata dall'AI.
- **Correlazioni e distribuzioni**: heatmap delle correlazioni tra le misure
  (con le coppie più forti) e istogramma della misura selezionata.
- **Insight automatici**: quota del leader, crescita di periodo, variazione
  recente e outlier — **numeri calcolati in Pandas**, non dedotti dall'AI.
- **Report esecutivo** generabile con un click (Executive Summary, Key Insights,
  Recommendations, Risks, Next Steps) e **scaricabile in Markdown**.
- **Grafici collegati (click-to-filter)**: nel report, cliccando una barra della
  classifica l'andamento temporale si filtra sulla categoria selezionata.
- **Confronto tra periodi**: aggrega una misura per mese, trimestre o anno con la
  **variazione percentuale** sul periodo precedente — dalla sezione dedicata o
  chiedendolo in una domanda (lo stesso motore deterministico alimenta entrambi).
- **Filtro persistente**: restringi l'intera analisi a un sottoinsieme (es. una
  regione) da un controllo nella barra laterale; resta attivo tra le domande e
  vale per report, KPI, confronto e chat.
- **Join tra due dataset**: carica un secondo file e uniscilo al primo su una
  coppia di chiavi; report e domande valgono poi sui dati uniti.
- **Esporta la conversazione** in Markdown: ogni turno con domanda, risultato,
  spiegazione e il **codice Pandas generato**.
- **Risposta testuale** a ogni domanda: l'AI interpreta il risultato calcolato e
  risponde citando i numeri chiave.
- **Unità di misura opzionale**: puoi indicarla dalla barra laterale; per le
  misure economiche senza unità indicata viene usato il dollaro come standard.
- **Esecuzione sicura**: il codice generato è validato staticamente con una
  **allowlist di nodi AST** (tutto ciò che non serve a un'espressione Pandas è
  rifiutato per costruzione) ed eseguito in un **sottoprocesso dedicato con
  timeout**, che restituisce al processo padre solo dati inerti. In container
  si aggiunge l'isolamento dal sistema.

## Esecuzione in locale

### 1. Requisiti
- **Python 3.12+** e **git**

### 2. Installazione
```bash
git clone https://github.com/raffaeleciccone-analyst/Natural-Language-Data-Analyst.git
cd Natural-Language-Data-Analyst

python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -e ".[all]"
```

Gli SDK dei provider sono **opzionali**: `[all]` li installa tutti, ma puoi
prendere solo quello che ti serve — `".[ollama]"`, `".[openai]"` (copre anche
Groq), `".[anthropic]"`, `".[gemini]"`. L'app parte comunque: l'import di ogni
SDK avviene solo quando scegli quel provider.

### 3. Scegli un modello LLM *(serve un LLM per generare le analisi)*

**Opzione A — Ollama in locale (gratuito, nessuna API key)**
1. Installa Ollama da <https://ollama.com>
2. Scarica il modello: `ollama pull qwen2.5:3b`
3. È già il provider predefinito.

**Opzione B — Provider cloud (API key)**
- Scegli Groq, Anthropic, OpenAI o Gemini dalla barra laterale e incolla la tua
  API key (oppure copia `.env.example` in `.env` e inseriscila lì).
- Groq offre una chiave gratuita (senza carta) su <https://console.groq.com/keys>.

### 4. Avvia
```bash
streamlit run main.py
```
L'app si apre nel browser su <http://localhost:8501>.

## Docker

```bash
GROQ_API_KEY=gsk_... docker compose up --build
```
L'app è su <http://localhost:8501>. Senza chiave parte comunque: puoi puntare a
un Ollama in locale o inserire la chiave dalla barra laterale.

Il container non serve solo alla portabilità: **è ciò che dà alla sandbox
l'isolamento dal sistema**. La validazione AST decide cosa il codice generato
può *dire*, il container decide cosa può *fare*. In `docker-compose.yml`:

- filesystem **in sola lettura** (le sole aree scrivibili sono tmpfs volatili);
- utente **non privilegiato** (uid 10001) e **tutte le capability rimosse**;
- `no-new-privileges`, tetto di RAM e limite di processi (niente fork-bomb);
- `ALLOW_INPROCESS_FALLBACK=false`: fail-closed, l'esecuzione si blocca invece
  di degradare a una sandbox più debole.

Chiude anche il residuo che l'AST non può coprire: una chiamata di libreria che
fa I/O al proprio interno (`px.data.gapminder()`) non è visibile al validatore,
ma resta confinata dal container.

## Usare i tuoi dati

All'avvio è già caricato un **dataset di esempio** (`data/sales.csv`, vendite
Superstore). Per usare i tuoi dati, carica un file CSV, Excel o JSON dalla barra
laterale: l'app rileva colonne, tipi e date automaticamente e adatta report,
KPI e domande al nuovo schema. Dalla sezione "Report" puoi scegliere la misura
e la categoria su cui basare KPI e classifiche; da "Filtro" restringi tutta
l'analisi a un sottoinsieme, e da "Unisci un secondo dataset" incroci due file
su una coppia di chiavi.

## Architettura

> 📐 Per **le decisioni di design e i loro trade-off** (perché allowlist e non
> denylist, perché il canale di ritorno è solo JSON, perché l'esito è un tipo…),
> vedi **[ARCHITECTURE.md](ARCHITECTURE.md)**. Qui sotto la mappa dei moduli.

```
main.py                    entry-point Streamlit sottile: configura la pagina e chiama main()
nlda/ui/pages.py           sezioni della pagina (sidebar, KPI, report, confronto, filtro, join, chat)
nlda/ui/session.py         plumbing UI: secret, quota demo, cache, caricamento e join dei dati
nlda/service.py            orchestrazione del turno (domanda → codice → esito → spiegazione)
nlda/agent.py              traduzione domanda → codice Pandas (adattata allo schema)
nlda/prompts/              i system prompt versionati (con golden a protezione)
nlda/sandbox/validator.py  allowlist di nodi AST: decide se il codice è ammissibile
nlda/sandbox/runner.py     esecuzione nel sottoprocesso e canale di ritorno JSON
nlda/sandbox/pool.py       riserva calda di worker (toglie il costo d'avvio dal percorso critico)
nlda/charts.py             figure Plotly e loro aspetto
nlda/periods.py            confronto tra periodi (mese/trimestre/anno) con variazione %
nlda/export.py             esportazione della conversazione in Markdown
nlda/loader.py             lettura multi-formato, profilo del dataset, analisi automatica
nlda/ui_components.py      componenti di presentazione Streamlit (card, tabelle, grafici)
nlda/results.py            esito tipizzato dell'esecuzione (successo / fallimento con causa)
nlda/pricing.py            stima del costo in USD di una chiamata (token → prezzo)
nlda/sanitize.py           difesa dai dati non fidati che finiscono nel prompt
nlda/errors.py             gerarchia di eccezioni applicative
nlda/demo.py               quota della demo pubblica
nlda/providers/            astrazione multi-LLM (ollama, groq, anthropic, openai, gemini)
nlda/config.py             configurazione centralizzata (timeout, sandbox, retry, log) da env
nlda/log.py                logging strutturato JSON con correlation-id e costo
scripts/                   favicon, registrazione del corpus, smoke ed eval
tests/                     suite pytest (con focus sulla sandbox di sicurezza)
```

Il flusso di una domanda: l'agente costruisce un prompt con lo schema reale del
dataset (nomi **e** valori delle colonne, sanitizzati), il provider LLM genera il
codice, l'executor lo valida con l'allowlist AST e lo esegue in un sottoprocesso
dedicato con timeout; se fallisce per un motivo correggibile il codice viene
rigenerato e ritentato, altrimenti il turno si chiude subito. Il risultato (dati
e/o figura) torna all'interfaccia insieme a un riepilogo usato dall'AI per la
risposta testuale.

## Sicurezza

> 🛡️ Per il **modello di minaccia completo** (superfici d'attacco, mitigazioni e
> *rischi residui* dichiarati, confini di fiducia) vedi **[THREAT_MODEL.md](THREAT_MODEL.md)**.

- **Whitelist di builtin** minimale nell'ambiente di esecuzione.
- **Analisi statica AST in allowlist**: il validatore ammette solo la manciata di
  nodi che servono a un'espressione Pandas (assegnazioni, chiamate, operatori,
  slice, comprehension, lambda). Tutto il resto è rifiutato *per costruzione* —
  `import`, `def`, `class`, `with`, `try`, `global`, `del`, `async`, walrus,
  `match` — compresi i costrutti che il linguaggio aggiungerà in futuro.
- Sui nodi ammessi valgono poi regole mirate: niente attributi privati/dunder,
  niente metodi di I/O (`to_*`/`read_*`/`write_*` su file o rete, con una
  whitelist di convertitori puri in memoria), niente esecuzione dinamica
  (`eval`, `exec`, `query`, `format`).
- **Sottoprocesso dedicato** con timeout: barriera di processo attorno al codice
  generato dall'LLM. È una barriera *di processo*, non di sistema: il worker
  gira come lo stesso utente dell'app. L'isolamento dal sistema lo aggiunge il
  container (vedi [Docker](#docker)).
- **Canale di ritorno non eseguibile**: il worker restituisce al processo padre
  soltanto JSON (dati inerti), mai oggetti serializzati con `pickle`. Il padre non
  ricostruisce quindi nulla di scelto dal processo che ha appena eseguito codice
  non fidato: se un domani la sandbox venisse forzata, la barriera di processo
  regge invece di cadere insieme a essa.
- **Limite di memoria del worker** tramite `RLIMIT_AS`. *Limite noto:* è
  disponibile solo su POSIX (Linux/macOS); **su Windows il modulo `resource` non
  esiste, quindi il worker è contenuto dal solo timeout**, non da un cap di RAM.
  Eseguire l'app nel container chiude anche questo: dentro il container il
  worker gira su Linux, quindi il cap torna attivo anche su host Windows.
- **Fail-closed opzionale**: se il sottoprocesso non è avviabile, l'esecuzione
  ripiega in-process (senza timeout né cap memoria). In deploy pubblico si
  disattiva il ripiego con `ALLOW_INPROCESS_FALLBACK=false`, così l'app blocca
  l'esecuzione invece di degradare la sandbox.
- I valori di esempio delle celle inseriti nel prompt sono **sanitizzati** per
  mitigare la prompt injection da file caricati.

## Sviluppo e qualità

```bash
pip install -e ".[all,dev]"
pytest        # test (inclusi i test di sicurezza sul validatore AST)
ruff check .  # lint
mypy nlda main.py  # type-check
```

Le dipendenze vivono in `pyproject.toml`. `requirements.txt` resta solo come
manifest di deploy: Streamlit Community Cloud installa esclusivamente da lì.

### Come si verifica un'app che dipende da un modello

Un test che sostituisce l'LLM con un finto non tocca né il testo del prompt né la
forma reale delle risposte: è un punto cieco in cui i difetti passano con la
suite verde. Qui la verifica è a strati, dal più deterministico al più incerto.

| Strato | Cosa garantisce | Dove |
|---|---|---|
| **Golden dei prompt** | nessuna modifica accidentale al testo delle istruzioni | `tests/test_prompt_contract.py` |
| **Contratti prompt ↔ runtime** | ciò che il prompt promette esiste, e il codice che insegna supera la nostra sandbox | idem |
| **Property/fuzz sul validatore** | migliaia di espressioni generate: nessun codice *accettato* accede a `__`/import/I-O (hypothesis) | `tests/test_validator_fuzz.py` |
| **Corpus rigiocato** | risposte reali registrate attraversano l'intera pipeline in modo deterministico | `tests/test_corpus_replay.py` |
| **Smoke** | con un modello vero, ogni domanda produce un risultato valido | `scripts/smoke.py` |
| **Eval** | le risposte sono *corrette*, non solo eseguibili | `scripts/eval.py` |

```bash
pytest                                      # i primi tre strati, in CI
python scripts/smoke.py                     # richiede un modello raggiungibile
python scripts/eval.py                      # punteggio di correttezza
python scripts/record_corpus.py             # rigenera il corpus registrato
```

Gli ultimi due parlano con un servizio esterno: girano in un workflow notturno
separato (`.github/workflows/smoke.yml`), non sulle pull request. L'eval in
particolare è una **misura**, non un test: il punteggio dipende dal modello e
oscilla. Serve a confrontare provider e ad accorgersi se un cambio al prompt
peggiora le risposte.

Ogni push esegue in CI (GitHub Actions, `.github/workflows/ci.yml`):

- **test** sulle tre versioni supportate (3.12, 3.13, 3.14);
- **lint e type-check** (ruff, mypy);
- **immagine Docker**: build, avvio e verifica che l'HEALTHCHECK diventi sano e
  che la sandbox funzioni dentro il container;
- **sicurezza**: `pip-audit` sulle dipendenze e `bandit` sul codice.

I parametri di runtime sono centralizzati in `nlda/config.py` e sovrascrivibili da
variabile d'ambiente, tra cui: `EXEC_TIMEOUT`, `MEMORY_LIMIT_MB`,
`ALLOW_INPROCESS_FALLBACK`, `LLM_REQUEST_TIMEOUT`, `LLM_MAX_RETRIES`, `LOG_LEVEL`,
`LOG_FORMAT` (`text` o `json`: il formato `json` emette una riga strutturata per
evento, con `turn_id`, latenza, token e costo stimato, pronta per un aggregatore
di log), `MAX_ROWS` e `MAX_COLUMNS` (limiti sul file caricato: sono soglie di
usabilita', non di memoria — oltre, ogni domanda richiede piu' di un secondo).

Con `LOG_FORMAT=json`, **`python scripts/analyze_logs.py <file>`** riepiloga i log
(tasso di successo, costo, percentili di latenza, esiti per causa, token): chiudere
il cerchio dell'osservabilità — non basta loggare, i log vanno letti.

## Deploy

Per pubblicare l'app (inclusa la modalità demo con limite di domande) vedi
[DEPLOY.md](DEPLOY.md).

## Licenza

Distribuito con licenza [MIT](LICENSE).
