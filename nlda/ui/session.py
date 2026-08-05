"""
Plumbing della UI: configurazione, quota della demo, caching e caricamento dati.

Tutto ciò che le sezioni della pagina (`nlda.ui.pages`) usano ma che NON è layout:
lettura dei secret, tetti di spesa della demo pubblica, cache del DataFrame e
dell'agente. Tenuto separato dalle `render_*` per non mescolare "come funziona"
con "come si mostra", e perché `main.py` ne ha bisogno tanto quanto le pagine.

Restano di proposito a livello di modulo le funzioni decorate con `@st.cache_*`:
definirle dentro una funzione le ricreerebbe a ogni rerun di Streamlit, la chiave
di cache cambierebbe ogni volta e la cache non verrebbe mai colpita.
"""
import os
from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from nlda.agent import DataAgent
from nlda.demo import DemoLimits
from nlda.demo_data import trova as trova_demo
from nlda.loader import NamedBytesIO, duplicate_columns_warning, load_dataset, read_any
from nlda.log import get_logger

log = get_logger(__name__)

_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
            "gemini": "GOOGLE_API_KEY", "groq": "GROQ_API_KEY"}


@dataclass(frozen=True)
class SidebarConfig:
    """Scelte dell'utente nella barra laterale, raccolte in un valore solo."""

    provider: str
    model_name: str
    api_key: str
    explain: bool
    uploaded_file: object | None
    demo_dataset: str = ""   # nome nel catalogo di `nlda.demo_data`, se non c'e' un upload


# --- Lettura della configurazione ---------------------------------------------
def _secret(key: str, default: str = "") -> str:
    """Legge un valore da st.secrets (deploy) o dalle variabili d'ambiente (locale)."""
    try:
        val = st.secrets.get(key)
        if val is not None:
            return str(val)
    except Exception:  # noqa: BLE001
        # Senza secrets.toml (uso locale) si ripiega sulle variabili d'ambiente:
        # è la condizione normale fuori dal deploy, non un errore da segnalare.
        pass  # nosec B110
    return os.getenv(key, default)


def _intero_da_secret(chiave: str, default: int) -> int:
    """Legge un intero dai secrets; un valore malformato non deve togliere il tetto."""
    try:
        return int(_secret(chiave, str(default)) or default)
    except ValueError:
        log.warning("Secret %s non è un intero: uso il default %d", chiave, default)
        return default


def demo_limits() -> DemoLimits:
    """Quota della demo pubblica, attivata dai secrets del deploy."""
    return DemoLimits(
        enabled=_secret("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on"),
        max_questions=_intero_da_secret("DEMO_MAX_QUESTIONS", 15),
        max_daily=_intero_da_secret("DEMO_MAX_DAILY", 200),
    )


@st.cache_resource
def _consumo_giornaliero() -> dict:
    """
    Contatore CONDIVISO fra tutte le sessioni del server, azzerato ogni giorno.

    `st.cache_resource` è una cache globale di processo: qui è esattamente ciò che
    serve — al contrario dell'agente, che tenevamo fuori di proposito perché
    contiene la API key. Un contatore non è un segreto.

    Il limite per sessione da solo non protegge il credito: basta aprire una
    scheda nuova per azzerarlo. Questo è il tetto di spesa vero.
    """
    return {"giorno": date.today(), "usate": 0}


def demo_allows(limits: DemoLimits, kind: str) -> bool:
    """True se resta budget su entrambi i limiti; altrimenti avvisa e blocca."""
    if not limits.enabled:
        return True

    consumo = _consumo_giornaliero()
    if consumo["giorno"] != date.today():   # nuovo giorno, nuovo credito
        consumo.update(giorno=date.today(), usate=0)

    if limits.has_budget(st.session_state.get("_demo_q", 0), consumo["usate"]):
        return True

    if consumo["usate"] >= limits.max_daily:
        st.warning("La demo ha esaurito il budget giornaliero condiviso. "
                   "Riprova domani, oppure clona il repo per uso illimitato.")
    else:
        st.warning(f"Hai raggiunto il limite della demo ({limits.max_questions} {kind}). "
                   "Clona il repo da GitHub per uso illimitato.")
    return False


def demo_consume(limits: DemoLimits) -> None:
    """
    Registra una richiesta che ha davvero raggiunto il modello.

    Si conta DOPO e non prima: se il provider è irraggiungibile non è stato speso
    nulla, e addebitare quella richiesta significherebbe far pagare all'utente un
    guasto nostro — per giunta su un tetto giornaliero condiviso, dove un
    provider giù consumerebbe l'intero credito senza produrre una risposta.
    """
    if not limits.enabled:
        return
    st.session_state["_demo_q"] = st.session_state.get("_demo_q", 0) + 1
    _consumo_giornaliero()["usate"] += 1


# --- Caricamento del DataFrame (con cache) ------------------------------------
# Streamlit ri-esegue l'intero script a ogni interazione: senza cache il file
# verrebbe riletto e ri-parsato a ogni click. @st.cache_data memoizza sul
# CONTENUTO (nome + byte), quindi la rilettura avviene solo se il file cambia.
@st.cache_data(show_spinner=False)
def load_uploaded_cached(name: str, data: bytes) -> pd.DataFrame:
    return read_any(NamedBytesIO(data, name))


@st.cache_data(show_spinner=False)
def load_default_cached(file_name: str = "sales.csv") -> pd.DataFrame:
    return load_dataset(file_name)


def get_agent(provider: str, model_name: str, api_key: str) -> DataAgent:
    """
    Agente per-sessione, ricreato solo se la configurazione cambia.

    Nota di sicurezza: NON si usa st.cache_resource, che è una cache condivisa fra
    tutte le sessioni del server: la API key finirebbe in memoria globale.
    """
    key = (provider, model_name or None, api_key or None)
    if st.session_state.get("_agent_key") != key:
        st.session_state["_agent_key"] = key
        st.session_state["_agent"] = DataAgent(
            provider=provider, model_name=model_name or None, api_key=api_key or None,
        )
    return st.session_state["_agent"]


def load_dataframe(uploaded_file, esempio: str | None = None):
    """
    Carica il file dell'utente o un dataset di esempio. Ritorna (df, etichetta).

    `esempio` e' il NOME di un dataset del catalogo (`nlda.demo_data`), lo stesso
    che usa l'API: le due interfacce offrono gli stessi esempi perche' leggono lo
    stesso elenco, non perche' qualcuno si ricorda di aggiornarne due.
    """
    if uploaded_file is not None:
        dati = uploaded_file.getvalue()
        try:
            df = load_uploaded_cached(uploaded_file.name, dati)
        except Exception as e:  # noqa: BLE001 — file dell'utente: qualunque errore va mostrato
            st.error(f"Errore nel caricamento del file: {e}")
            return None, None
        # Due colonne con lo stesso nome: pandas rinomina la seconda in silenzio.
        # Lo stesso avviso che l'API mette nella risposta, composto dalla stessa
        # funzione: la differenza fra le due interfacce dev'essere solo il modo
        # di mostrarlo.
        avviso = duplicate_columns_warning(dati, uploaded_file.name)
        if avviso:
            st.warning(f"⚠️ {avviso}")
        return df, f"File caricato: {uploaded_file.name}"
    scelto = trova_demo(esempio)
    if scelto is None:
        return None, None
    try:
        return load_default_cached(scelto.file), f"Esempio · {scelto.etichetta}"
    except FileNotFoundError:
        return None, None


def load_second_dataset(uploaded_file):
    """Carica il secondo file per un join (con cache), o None se non caricato."""
    if uploaded_file is None:
        return None
    return load_uploaded_cached(uploaded_file.name, uploaded_file.getvalue())
