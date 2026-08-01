# syntax=docker/dockerfile:1

# =============================================================================
# Oltre alla portabilità, il container è ciò che dà alla sandbox l'ISOLAMENTO
# DAL SISTEMA che il solo sottoprocesso non può offrire: il codice generato
# dall'LLM gira in un filesystem separato, come utente non privilegiato e dentro
# i limiti del container. La validazione AST vede la sintassi, non l'I/O che una
# libreria compie al proprio interno (px.data.gapminder() legge un file): quel
# residuo lo chiude il confine del container, non il validatore.
#
# Su host Windows chiude anche un limite dichiarato nel README: dentro il
# container il worker gira su Linux, quindi RLIMIT_AS funziona e il cap di
# memoria torna attivo.
# =============================================================================

# ---- Stage 1: frontend -------------------------------------------------------
# Il build di Vite avviene QUI e non sulla macchina di chi costruisce: cosi'
# l'immagine non dipende da quale Node abbia in locale, e `dist/` non va
# committato. Node serve solo a compilare — nell'immagine finale non entra.
FROM node:25-slim AS frontend

WORKDIR /fe
# Prima solo i manifest: se non cambiano, Docker riusa il livello con le
# dipendenze gia' installate anche quando cambia il codice sorgente.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---- Stage 2: dipendenze Python ----------------------------------------------
FROM python:3.14-slim AS builder

ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# Quali extra installare. 'all' sono tutti i provider, 'api' e' FastAPI+uvicorn
# (senza, il comando di default non parte). Per un deploy si puo' restringere:
#   --build-arg EXTRAS=openai,api   -> la sola demo su Groq
ARG EXTRAS=all,api

COPY pyproject.toml README.md ./
COPY nlda ./nlda
# Risolve le dipendenze da pyproject, poi disinstalla il pacchetto: nel venv
# restano le sole dipendenze e l'applicazione arriva dal sorgente in /app, dove
# i percorsi relativi (data/, assets/) sono quelli che il codice si aspetta.
RUN pip install ".[${EXTRAS}]" \
 && pip uninstall -y natural-language-data-analyst

# ---- Stage 3: runtime --------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOME=/home/app
ENV STREAMLIT_SERVER_HEADLESS=true
# Fail-closed: se il sottoprocesso non è avviabile l'esecuzione si blocca invece
# di degradare all'in-process, che non ha né timeout né cap di memoria.
ENV ALLOW_INPROCESS_FALLBACK=false

COPY --from=builder /opt/venv /opt/venv

# Utente non privilegiato: il codice generato dall'LLM non deve mai girare da root.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY --chown=app:app main.py ./
COPY --chown=app:app nlda ./nlda
COPY --chown=app:app data ./data
COPY --chown=app:app assets ./assets
COPY --chown=app:app .streamlit ./.streamlit
# Il frontend compilato, dove `nlda/api/app.py` lo cerca.
COPY --from=frontend --chown=app:app /fe/dist ./frontend/dist

# La base di conoscenza di "Chiedi al progetto" (`nlda/project_qa.py:FONTI`).
# Senza, nel container la ricerca non trovava NULLA e ogni domanda riceveva
# "non trovo nulla nella documentazione": una fonte assente è trattata come non
# fatale — giustamente, perché il resto dell'app funziona — quindi il difetto si
# annunciava solo con sei warning nei log che nessuno guardava.
COPY --chown=app:app README.md ARCHITECTURE.md THREAT_MODEL.md VALUE.md DEPLOY.md ./
COPY --chown=app:app docs/DOCUMENTAZIONE_TECNICA.md ./docs/

USER app
# 8501 = interfaccia Streamlit, 8000 = API + frontend React. Due interfacce
# sullo stesso backend: si sceglie quale avviare col comando, non ricostruendo
# l'immagine.
EXPOSE 8000 8501

# La porta è configurabile perché i PaaS la impongono: Render, Fly e Cloud Run
# passano `$PORT` e si aspettano che il processo ascolti lì. In locale resta 8000
# e nulla cambia.
ENV PORT=8000

# Il controllo di salute interroga l'API, che è ciò che il comando di default
# avvia. L'immagine slim non ha curl: si usa l'interprete che c'è già invece di
# installare un pacchetto in più solo per questo.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen(f\"http://localhost:{os.environ['PORT']}/api/health\", timeout=4).status == 200 else 1)"

# Di default parte l'API, che serve anche il frontend React compilato. Per
# l'interfaccia Streamlit si passa un altro comando allo stesso container:
#   docker run ... nlda streamlit run main.py --server.address=0.0.0.0 --server.port=8501
# Due interfacce sullo stesso backend, una sola immagine.
#
# Forma shell (`sh -c`) perché la forma exec non espande `$PORT`. L'`exec` davanti
# a uvicorn è necessario: senza, la shell resta il processo 1 e il SIGTERM
# dell'orchestratore arriva a lei invece che a uvicorn, che verrebbe ucciso solo
# allo scadere del timeout di grazia — a ogni riavvio, dieci secondi buttati.
CMD ["sh", "-c", "exec uvicorn nlda.api.app:app --host 0.0.0.0 --port ${PORT}"]
