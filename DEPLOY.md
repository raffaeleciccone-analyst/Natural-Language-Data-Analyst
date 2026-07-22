# Deploy della demo pubblica (per il manutentore)

Istruzioni per pubblicare l'app su **Streamlit Community Cloud**, così chiunque può
provarla dal browser senza installare nulla. Questo file è per chi mantiene il
progetto, non per gli utenti finali.

## Passi

1. Vai su <https://share.streamlit.io> e accedi con GitHub.
2. **New app** → seleziona questo repo, branch `main`, file `main.py`.
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
4. **Deploy**. In modalità demo l'app usa la chiave dei secrets, nasconde i campi
   sensibili e applica **due** limiti: uno per sessione e un tetto
   giornaliero condiviso, che è quello che protegge davvero il credito
   (il limite per sessione si azzera aprendo una scheda nuova).
5. Copia il link pubblico ottenuto e inseriscilo in cima al `README.md`
   (riga commentata `▶️ **Prova la demo:** <URL>`).

## Costi e chiave

- 💡 **Gratis (consigliato)**: **Groq** ha un tier gratuito affidabile (senza carta)
  da <https://console.groq.com/keys> — inferenza veloce su modelli Llama.
- Alternative: **Gemini** (`gemini-2.0-flash`, free tier se disponibile sul tuo
  account) da <https://aistudio.google.com/apikey>; a pagamento `gpt-4o-mini`
  (OpenAI) o `claude-haiku-4-5` (Anthropic) con un **tetto di spesa** sul provider.
- La chiave va **solo** nei Secrets di Streamlit Cloud, mai nel repo.
- Ollama non è disponibile sul cloud (gira solo in locale).

## Deploy con Docker (qualsiasi host)

Streamlit Community Cloud è comodo ma non permette di scegliere i limiti di
sistema. Su una VM o un PaaS che accetti container:

```bash
GROQ_API_KEY=gsk_... docker compose up -d --build
```

L'immagine espone un `HEALTHCHECK` su `/_stcore/health`, quindi orchestratori e
load balancer sanno quando l'app è pronta.

## Sicurezza per un deploy pubblico

Il codice generato è validato staticamente (allowlist di nodi AST) ed eseguito in
un sottoprocesso dedicato con timeout, che restituisce al padre solo JSON. In
modalità demo c'è anche un limite di domande per sessione.

Il sottoprocesso però è una barriera *di processo*: gira come lo stesso utente
dell'app, sullo stesso filesystem. Per un uso pubblico con **dati non fidati** il
deploy in container è la configurazione consigliata, perché aggiunge ciò che il
codice applicativo non può darsi da solo: filesystem in sola lettura, utente non
privilegiato, capability rimosse, tetto di RAM e di processi. Vedi
`docker-compose.yml`.
