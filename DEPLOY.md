# Deploy delle demo pubbliche (per il manutentore)

Il progetto ha **due interfacce sullo stesso backend**, e si pubblicano in due
posti diversi perché hanno bisogni diversi:

| Interfaccia | Cosa serve | Dove |
|---|---|---|
| **React + API FastAPI** | un host che sappia far girare un container | [Render](#1-interfaccia-react--api-render) |
| **Streamlit** | `streamlit run main.py` | [Streamlit Community Cloud](#2-interfaccia-streamlit-streamlit-cloud) |

Streamlit Cloud **non** può servire l'interfaccia React: sa avviare solo
`streamlit run`, non un processo arbitrario. Ecco perché due host.

> ⚠️ **Prima di pubblicare, leggi [Il tetto di spesa](#il-tetto-di-spesa).** Un
> deploy pubblico gira con *la tua* chiave: senza `DEMO_MODE` la paghi tu, per
> chiunque passi.

---

## 1. Interfaccia React + API (Render)

La configurazione sta in [`render.yaml`](render.yaml), versionata accanto al
codice che descrive. Non è per eleganza: la configurazione che vive solo in un
pannello web non è rivedibile, non è ripristinabile e non si sa chi l'ha cambiata.

### Passi

1. Vai su <https://dashboard.render.com> e accedi con GitHub.
2. **New → Blueprint** → scegli questo repo. Render legge `render.yaml` e propone
   il servizio già configurato.
3. L'unico valore da inserire a mano è **`GROQ_API_KEY`** (in `render.yaml` è
   marcato `sync: false` proprio perché un file versionato è pubblico quanto il
   repo). Render lo chiede **solo alla creazione**: se in seguito va cambiato, si
   modifica dalla pagina *Environment* del servizio, non da qui.
4. **Apply**. Il primo build richiede qualche minuto: compila il frontend con
   Vite e poi costruisce l'immagine Python.
5. Copia l'URL ottenuto e mettilo in cima al [`README.md`](README.md).

### Cosa aspettarsi dal piano gratuito

- **512 MB di RAM.** Bastano: l'immagine usa ~100 MB a riposo e ~130 MB con un
  dataset da 10.000 righe caricato e il report calcolato (misurato con
  `docker stats`).
- **Si spegne dopo 15 minuti di inattività.** La prima visita dopo una pausa
  aspetta il riavvio (~1 minuto). È il compromesso del piano gratuito, non un
  problema dell'app.
- **Il disco è effimero.** Va bene: i dataset caricati vivono già solo in memoria
  (`nlda/api/store.py`), con scadenza a un'ora.

### Tenerla sveglia negli orari che contano

Lo spegnimento si evita mandando traffico, ma **non 24 ore su 24**. L'aritmetica:
il piano gratuito dà **750 ore-istanza al mese per workspace**, un mese di 31
giorni ne ha **744**, e un servizio spento non ne consuma. Tenerla sveglia sempre
significherebbe vivere a sei ore dal tetto — e superarlo sospende *tutti* i
servizi gratuiti del workspace fino al primo del mese.

Si sveglia quindi solo nella finestra in cui qualcuno la aprirebbe davvero:
**lunedì-venerdì, 7:00-22:00**. Sono ~330 ore al mese, con un margine largo.

Configurazione su <https://cron-job.org> (gratuito):

| Campo | Valore |
|---|---|
| URL | `https://nlda.onrender.com/api/health` |
| Intervallo | ogni **10 minuti** |
| Ore | 7-21 |
| Giorni | lunedì-venerdì |
| Fuso | Europe/Rome |

Tre dettagli che non sono arbitrari:

- **`/api/health` e non la homepage**: risponde in 0,2 s, non calcola nulla e non
  tocca la quota delle domande, che conta solo le rotte che chiamano il modello.
- **10 minuti e non 14**: lo spegnimento scatta a 15 minuti di silenzio, e un
  ping perso non deve bastare a farla addormentare.
- **Dalle 7 e non dalle 8**: il risveglio costa un minuto, quindi il primo ping
  deve arrivare *prima* dell'orario in cui la vuoi già pronta.

**UptimeRobot non va bene per questo**: nel piano gratuito le finestre di
manutenzione non ci sono, quindi controllerebbe 24/7 — cioè proprio lo scenario
che l'aritmetica sopra sconsiglia.

Render non dichiara se tenere sveglio un servizio gratuito sia permesso o
scoraggiato: la documentazione non tocca il punto. Con la finestra oraria si resta
comunque ampiamente dentro il consumo che il piano prevede.

### Verificare il container prima di pubblicarlo

Vale la pena farlo in locale: gli errori di deploy sono i più lenti da diagnosticare
a distanza.

```bash
docker build -t nlda .
docker run --rm -p 8000:8000 \
  -e DEMO_MODE=true -e DEMO_MAX_QUESTIONS=2 \
  -e PROVIDER=groq -e MODEL=llama-3.3-70b-versatile \
  -e GROQ_API_KEY=gsk_... \
  nlda
```

Poi su <http://localhost:8000>: la pagina React deve comparire, `/api/health`
rispondere `{"status":"ok"}`, e la **terza** domanda tornare `429`.

Per provare una porta diversa — è ciò che fa Render — basta `-e PORT=9999` e
pubblicare quella: il `HEALTHCHECK` la segue.

---

## 2. Interfaccia Streamlit (Streamlit Cloud)

1. Vai su <https://share.streamlit.io> e accedi con GitHub.
2. **New app** → questo repo, branch `main`, file `main.py`.
3. In **Advanced settings → Secrets** incolla la configurazione
   (vedi [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example)):
   ```toml
   DEMO_MODE = "true"
   DEMO_MAX_QUESTIONS = "15"
   DEMO_MAX_DAILY = "200"
   PROVIDER = "groq"
   MODEL = "llama-3.3-70b-versatile"
   GROQ_API_KEY = "gsk_..."
   ```
4. **Deploy**, poi copia il link nel `README.md`.

Le variabili hanno **gli stessi nomi** dell'API di proposito: un secondo
vocabolario per la stessa regola vorrebbe dire configurare due volte lo stesso
deploy, e accorgersi dalla bolletta di averne dimenticata una.

---

## Il tetto di spesa

Un deploy pubblico usa una chiave del manutentore. Senza limiti, ogni visitatore
ne spende il credito — e non serve malafede: bastano dieci curiosi.

`DEMO_MODE=true` attiva **due** limiti, che servono a cose diverse:

- **Per visitatore** (`DEMO_MAX_QUESTIONS`) — evita che uno solo esaurisca la
  giornata di tutti. Da solo non protegge nulla: lato Streamlit basta una scheda
  nuova, lato API basta un altro indirizzo IP.
- **Giornaliero condiviso** (`DEMO_MAX_DAILY`) — **è il tetto vero.** Vale su
  tutte le richieste del processo e si azzera ogni giorno, così una giornata
  storta non brucia il credito e domani la demo è di nuovo viva.

Nessuno dei due ferma un abuso determinato: per quello servirebbe un'identità,
che una demo pubblica non ha. Servono a contenere il costo, ed è quello che
dichiarano di fare.

**Due limiti noti, dichiarati perché non si scoprano dal conto del provider:**

- Il contatore vive **in memoria del processo**. Con più repliche ognuna ha il
  proprio tetto, quindi la spesa massima si moltiplica per il numero di repliche.
  Per una singola istanza — la configurazione di `render.yaml` — è corretto.
- Lato API il "visitatore" è **un indirizzo IP**, letto da `X-Forwarded-For`.
  Dietro NAT molte persone lo condividono, e chi ne cambia uno riparte da zero.
  È il motivo per cui il tetto che conta è quello giornaliero, che nessuna
  intestazione può spostare.

Chi porta la **propria** chiave API non tocca il budget: sta spendendo il proprio
credito. In modalità demo l'interfaccia React lo dice esplicitamente al posto del
menu del modello, e quella Streamlit nasconde del tutto i campi sensibili.

### Costi e chiave

- 💡 **Gratis (consigliato)**: **Groq** ha un tier gratuito affidabile (senza carta)
  da <https://console.groq.com/keys> — inferenza veloce su modelli Llama.
- Alternative: **Gemini** (`gemini-2.0-flash`) da <https://aistudio.google.com/apikey>;
  a pagamento `gpt-4o-mini` (OpenAI) o `claude-haiku-4-5` (Anthropic), con un
  **tetto di spesa** impostato sul provider.
- La chiave va **solo** nei secret dell'host, mai nel repo.
- Ollama non è disponibile sul cloud: gira solo in locale.

Con `LOG_FORMAT=json` ogni turno emette una riga strutturata con latenza, token e
costo stimato; **`python scripts/analyze_logs.py <file>`** la riepiloga. Serve a
sapere quanto è costata davvero una settimana di demo, invece di dedurlo.

---

## Sicurezza di un deploy pubblico

Il codice generato è validato staticamente (allowlist di nodi AST) ed eseguito in
un sottoprocesso dedicato con timeout, che restituisce al padre solo JSON.

Il sottoprocesso però è una barriera *di processo*: gira come lo stesso utente
dell'app, sullo stesso filesystem. Con **dati non fidati** — ed è la definizione
di una demo pubblica — il container è la configurazione da usare, perché aggiunge
ciò che il codice applicativo non può darsi da solo: filesystem in sola lettura,
utente non privilegiato, capability rimosse, tetto di RAM e di processi. Vedi
[`docker-compose.yml`](docker-compose.yml) e il commento in testa al
[`Dockerfile`](Dockerfile).

`ALLOW_INPROCESS_FALLBACK=false` (già in `render.yaml`) chiude l'ultima porta: se
il sottoprocesso non è avviabile l'esecuzione **si blocca** invece di ripiegare
in-process, che non ha né timeout né tetto di memoria. Fallire chiuso.
