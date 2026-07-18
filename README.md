# AI Data Analyst Assistant

**▶️ Demo online:** _inserisci qui il link a Streamlit Cloud dopo il deploy_

Interroga i tuoi dati in **linguaggio naturale**. Fai una domanda (es. *"Qual è il
mese con più vendite?"* o *"Mostrami le vendite per regione"*), un modello LLM
traduce la richiesta in codice Pandas, l'app lo esegue in una sandbox e ti mostra
**il risultato, un grafico interattivo e una risposta testuale** che lo interpreta.

Al caricamento di un file ricevi anche un **report automatico** con statistiche,
classifiche e andamento temporale dei dati.

## Anteprima

Anteprime del design (light/dark) — sostituibili con screenshot reali dell'app
(vedi [docs/README.md](docs/README.md)).

![Report iniziale sui dati](docs/preview-report.svg)
![Tema scuro con risposta](docs/preview-dark.svg)

## Cosa fa

- 📊 Domande in italiano → codice Pandas → grafici **Plotly** interattivi
- 🧠 Multi-provider LLM: **Ollama** (locale), **Anthropic**, **OpenAI**, **Gemini**
- 📁 Carica **CSV, Excel (.xlsx) o JSON** — si adatta a qualsiasi schema
- 📋 Report iniziale automatico + risposta testuale a ogni domanda
- 🌙 Tema chiaro/scuro
- 🔒 Il codice generato gira in una **sandbox** (validazione AST) dentro un
  **sottoprocesso isolato con timeout**

---

## Provalo in locale

### 1. Requisiti
- **Python 3.10+**
- **git**

### 2. Installazione
```bash
git clone https://github.com/<utente>/<repo>.git
cd <repo>

python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Scegli un modello LLM  *(passo obbligatorio: serve un LLM)*

**Opzione A — Ollama in locale (gratis, nessuna API key) — consigliata per provare**
1. Installa Ollama da <https://ollama.com>
2. Scarica il modello di default:
   ```bash
   ollama pull qwen2.5:3b
   ```
3. Fatto: è già il provider predefinito dell'app.

**Opzione B — Provider cloud (serve una API key)**
- Scegli **Anthropic**, **OpenAI** o **Gemini** dalla barra laterale.
- Incolla la tua API key nella sidebar, **oppure** copia `.env.example` in `.env`
  e inserisci `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`.

### 4. Avvia
```bash
streamlit run main.py
```
Si apre nel browser su <http://localhost:8501>.

---

## Usare i tuoi dati
- All'avvio è già caricato un **dataset di esempio** (`data/sales.csv`, vendite Superstore).
- Per usare i tuoi: **carica un CSV/Excel/JSON** dalla barra laterale. L'app rileva
  colonne, tipi e date automaticamente.

## Demo pubblica su Streamlit Cloud (senza installare nulla)

Per far provare l'app a chiunque dal browser, con un link:

1. Vai su <https://share.streamlit.io> e accedi con GitHub.
2. **New app** → seleziona questo repo, branch `main`, file `main.py`.
3. In **Advanced settings → Secrets** incolla la configurazione (vedi
   [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example)):
   ```toml
   DEMO_MODE = "true"
   DEMO_MAX_QUESTIONS = "15"
   PROVIDER = "gemini"
   MODEL = "gemini-2.5-flash"
   GOOGLE_API_KEY = "AIza..."
   ```
4. **Deploy**. In modalità demo l'app usa la tua chiave, nasconde i campi sensibili
   e limita le domande per sessione.

> 💡 **Gratis**: la chiave **Google Gemini** ha un tier gratuito (senza carta) da
> <https://aistudio.google.com/apikey> — perfetta per una demo a costo zero
> (con rate limit). In alternativa, a pagamento, `gpt-4o-mini` o `claude-haiku-4-5`;
> in quel caso imposta un **tetto di spesa** sul pannello del provider.
> Ollama non è disponibile sul cloud (gira solo in locale).

## Struttura
```
main.py            interfaccia Streamlit (chat, KPI, report, tema)
core/loader.py     lettura file multi-formato + profilo e analisi dei dati
core/agent.py      traduzione domanda → codice Pandas (adattata allo schema)
core/executor.py   sandbox + esecuzione isolata + grafici Plotly
core/providers/    astrazione multi-LLM (ollama/anthropic/openai/gemini)
```

## Note
- **Uso previsto: locale.** La sandbox blocca I/O su file, import e costrutti
  pericolosi ed esegue in un sottoprocesso con timeout. Per un **deploy pubblico**
  con dati non fidati valuta ulteriori limiti (memoria/rete a livello di sistema).
- Un mockup del design dell'interfaccia è in [`design/dashboard.html`](design/dashboard.html).
