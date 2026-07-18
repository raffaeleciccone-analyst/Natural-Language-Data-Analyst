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
   PROVIDER = "gemini"
   MODEL = "gemini-2.5-flash"
   GOOGLE_API_KEY = "AIza..."
   ```
4. **Deploy**. In modalità demo l'app usa la chiave dei secrets, nasconde i campi
   sensibili e limita le domande per sessione.
5. Copia il link pubblico ottenuto e inseriscilo in cima al `README.md`
   (riga commentata `▶️ **Prova la demo:** <URL>`).

## Costi e chiave

- 💡 **Gratis**: la chiave **Google Gemini** ha un tier gratuito (senza carta) da
  <https://aistudio.google.com/apikey> — adatta a una demo a costo zero (rate limit).
- Alternative a pagamento: `gpt-4o-mini` (OpenAI) o `claude-haiku-4-5` (Anthropic);
  in tal caso imposta un **tetto di spesa** sul pannello del provider.
- La chiave va **solo** nei Secrets di Streamlit Cloud, mai nel repo.
- Ollama non è disponibile sul cloud (gira solo in locale).

## Sicurezza per un deploy pubblico

Il codice generato gira già in una sandbox (validazione AST) dentro un sottoprocesso
isolato con timeout, e in modalità demo c'è un limite di domande per sessione.
Per un uso pubblico con **dati non fidati** valuta ulteriori limiti a livello di
sistema (memoria/rete del container). Vedi anche [HANDOFF.md](HANDOFF.md).
