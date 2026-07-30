"""
API HTTP del progetto: lo stesso backend, servito a un frontend qualunque.

## Perché esiste

L'applicazione Streamlit e questa API chiamano ESATTAMENTE le stesse funzioni:
`loader` per i numeri, `AnalysisService` per un turno, `sandbox` per eseguire.
Nessuna logica è duplicata qui — se lo fosse, le due interfacce comincerebbero a
rispondere in modo diverso alla stessa domanda, ed è il difetto che rende inutili
le architetture "a strati" dichiarate ma non rispettate.

Che questo modulo sia corto è la prova che la stratificazione era vera: il lavoro
è tutto in `nlda/`, qui c'è solo traduzione da e verso JSON.

## Le scelte che vale la pena difendere

* **Nessuna sessione.** Il dataset vive in `store.magazzino`, indirizzato
  dall'impronta del suo contenuto. L'API resta senza stato per richiesta: due
  domande consecutive non devono arrivare allo stesso processo *logico*, solo
  trovare il dataset in memoria (limite dichiarato in `store.py`).
* **La chiave API non si registra e non si logga.** Arriva nell'header della
  richiesta, viene passata al provider e finisce lì. Non entra nel magazzino, non
  compare nei log, non torna indietro in nessuna risposta.
* **Gli errori attesi non sono eccezioni.** Un codice rifiutato dalla sandbox o un
  timeout tornano con HTTP 200 e `ok: false`, perché sono ESITI dell'operazione,
  non guasti del servizio. Il 4xx/5xx resta per ciò che è davvero un errore di
  chiamata (dataset inesistente, file illeggibile). È la stessa distinzione che il
  backend fa fra `ExecutionFailure` ed eccezione.
"""
import json
import os
from pathlib import Path
from typing import Annotated, cast

import pandas as pd
from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from nlda import charts, checks
from nlda.agent import DataAgent
from nlda.api import quota, store
from nlda.api.models import (
    AskRequest,
    AskResponse,
    ColumnInfo,
    ColumnKind,
    ConfigResponse,
    DatasetResponse,
    DistinctResponse,
    ErrorResponse,
    ExecutiveReportResponse,
    ExportRequest,
    ExportResponse,
    FiltroSpec,
    JoinRequest,
    Kpi,
    OverviewResponse,
    PeriodRow,
    PeriodsResponse,
    ProjectQaRequest,
    ProjectQaResponse,
    ProviderInfo,
    ReportResponse,
    ValueKind,
)
from nlda.api.streaming import trasmetti
from nlda.config import settings
from nlda.export import conversation_to_markdown
from nlda.kpis import build_kpis
from nlda.loader import (
    SUPPORTED_EXTENSIONS,
    NamedBytesIO,
    analyze,
    best_category,
    category_columns,
    date_columns,
    default_unit,
    load_dataset,
    measure_columns,
    ordered_measures,
    profile,
    read_any,
)
from nlda.log import get_logger
from nlda.periods import compare_periods
from nlda.project_qa import answer as project_answer
from nlda.providers import DEFAULT_MODELS, REQUIRES_API_KEY, available_providers, get_provider
from nlda.results import ExecutionSuccess, advice_for
from nlda.service import AnalysisService, Turn
from nlda.suggestions import FREQUENCIES, PROJECT_QUESTIONS, example_questions
from nlda.utils import with_unit
from nlda.views import apply_filter, join_datasets

log = get_logger(__name__)

# Le risposte d'errore si DICHIARANO, non solo si producono. Il gestore più in
# basso le formatta tutte allo stesso modo, ma FastAPI mette nello schema OpenAPI
# soltanto i tipi che vede citati in una rotta: senza questa dichiarazione
# `ErrorResponse` non finiva nello schema, quindi non finiva nei tipi TypeScript —
# e il frontend si sarebbe trovato senza tipo proprio per la forma che incontra a
# ogni chiamata andata storta.
_ERRORI = {
    400: {"model": ErrorResponse, "description": "Richiesta non valida"},
    404: {"model": ErrorResponse, "description": "Dataset non trovato o scaduto"},
    413: {"model": ErrorResponse, "description": "File troppo grande"},
}
router = APIRouter(prefix="/api", responses=_ERRORI)  # type: ignore[arg-type]

MAX_UPLOAD_MB = 25   # lo stesso tetto della UI: un limite per interfaccia sarebbe una bugia

# Il tetto di spesa della demo pubblica. Fuori dalla demo (`DEMO_MODE` assente)
# non conta nulla e non costa nulla: `Quota.consuma` esce alla prima riga.
_quota = quota.Quota(quota.limiti_da_ambiente())


def _consuma_quota(request: Request, api_key: str | None) -> None:
    """
    Scala una richiesta dal budget della demo, o solleva 429 se è finito.

    Chi porta la PROPRIA chiave non tocca il budget: sta spendendo il proprio
    credito, e limitarlo sarebbe un limite senza scopo. L'app Streamlit non ha
    questo caso perché in modalità demo nasconde del tutto il campo della chiave;
    l'API non può nasconderlo, quindi lo tratta.
    """
    if api_key:
        return
    _quota.consuma(quota.visitatore(request))


def _figura_json(fig) -> dict:
    """
    Figura Plotly come JSON puro.

    NON si usa `to_plotly_json()`: conserva gli array numpy, che Pydantic non sa
    serializzare. `to_json()` è il serializzatore nativo di Plotly — lo stesso che
    il progetto già usa per far viaggiare le figure fuori dalla sandbox — e
    restituisce una stringa già corretta, che qui si rilegge come dizionario.
    """
    return json.loads(fig.to_json())


def _righe_json(df: pd.DataFrame, massimo: int = 200) -> list[dict]:
    """
    Righe di un DataFrame come JSON puro.

    `to_dict("records")` lascerebbe dentro `numpy.int64`, `Timestamp` e `NaN` —
    tutti tipi che il serializzatore JSON rifiuta o traduce in `NaN` letterale, che
    non è JSON valido. `to_json` di pandas li converte tutti, date comprese.
    """
    return json.loads(df.head(massimo).to_json(orient="records", date_format="iso"))


MAX_VALORI_DISTINTI = 500   # oltre, un elenco a discesa non e' piu' usabile


def _filtrato(df: pd.DataFrame,
              filtro: "FiltroSpec | None") -> "tuple[pd.DataFrame, str]":
    """
    Applica il filtro e ne restituisce anche l'ETICHETTA leggibile.

    Il filtro vale per il report E per le domande: se restringessi solo il primo,
    l'utente vedrebbe numeri di un sottoinsieme e riceverebbe risposte sul totale
    — due verita' diverse nella stessa pagina.

    L'etichetta la compone gia' `views.apply_filter`, che distingue il caso
    singolo (`col = v`) dal multiplo (`col in {a, b}`). Prima la si buttava via e
    il client ne scriveva una terza forma: lo stesso filtro si leggeva in due modi
    a seconda dell'interfaccia.
    """
    if filtro is None:
        return df, ""
    _esigi_colonne(df, filter_column=filtro.column)
    return apply_filter(df, (filtro.column, tuple(filtro.values)))


def _esigi_colonne(df: pd.DataFrame, **colonne: str) -> None:
    """
    400 se una colonna nominata dal client non esiste, con lo STESSO messaggio
    ovunque. Il nome del parametro fa da etichetta, cosi' chi legge l'errore sa
    quale campo della richiesta correggere.

    Prima il controllo era ripetuto in quattro rotte con tre messaggi diversi: chi
    integra l'API imparava a riconoscerne uno e trovava gli altri.
    """
    for parametro, nome in colonne.items():
        if nome not in df.columns:
            raise HTTPException(
                status_code=400,
                detail=f"{parametro}: la colonna '{nome}' non esiste in questo dataset.")


def _dataset(dataset_id: str) -> store.Voce:
    """La voce richiesta, o un 404 che dice cosa fare — non un KeyError."""
    voce = store.magazzino.prendi(dataset_id)
    if voce is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset non trovato o scaduto: ricaricalo. "
                   "I dati caricati restano in memoria per un'ora.")
    return voce


def _agente(provider: str | None, model: str | None, api_key: str | None) -> DataAgent:
    """
    Costruisce l'agente per QUESTA richiesta.

    Non si mette in cache: conterrebbe la chiave API di chi ha chiamato, e una
    cache condivisa fra richieste la esporrebbe alle altre. È lo stesso motivo per
    cui l'app Streamlit non usa `st.cache_resource` per l'agente — la trappola non
    cambia solo perché cambia il framework.
    """
    nome, modello = _scelta_modello(provider, model)
    return DataAgent(provider=nome, model_name=modello, api_key=api_key or None)


def _scelta_modello(provider: str | None, model: str | None) -> tuple[str, str | None]:
    """
    Provider e modello effettivi per questa richiesta.

    `MODEL` descrive il modello del provider PREDEFINITO: applicarlo anche a un
    provider scelto dal client vorrebbe dire mandare a Anthropic il nome di un
    modello Groq, cioè un 404 che non spiega nulla. Per questo vale solo quando
    la richiesta non ha scelto né l'uno né l'altro.
    """
    nome = (provider or _provider_predefinito()).lower()
    if nome not in DEFAULT_MODELS:
        raise HTTPException(status_code=400, detail=f"Provider sconosciuto: '{nome}'.")
    if not model and not provider:
        model = os.getenv("MODEL", "").strip()
    return nome, model or None


def _provider_predefinito() -> str:
    """
    Il provider da usare quando il client non ne indica uno.

    `PROVIDER` viene PRIMA dell'euristica, come nell'app Streamlit
    (`pages.render_sidebar`), e per una ragione che si vede solo in deploy:
    `available_providers()` elenca quelli che il progetto SUPPORTA, non quelli
    raggiungibili. Senza la variabile la risposta era sempre "ollama" — corretta
    in locale, sbagliata su un host dove Ollama non esiste, dove ogni domanda
    falliva con un errore di connessione invece di usare la chiave configurata.
    """
    scelto = os.getenv("PROVIDER", "").strip().lower()
    if scelto in DEFAULT_MODELS:
        return scelto
    if scelto:
        log.warning("PROVIDER='%s' non e' un provider noto: si ignora.", scelto)
    disponibili = available_providers()
    return "ollama" if "ollama" in disponibili else disponibili[0]


# --- Configurazione -----------------------------------------------------------
@router.get("/config", response_model=ConfigResponse, summary="Cosa permette questa installazione")
def config() -> ConfigResponse:
    return ConfigResponse(
        providers=[ProviderInfo(name=p, default_model=DEFAULT_MODELS[p],
                                requires_api_key=p in REQUIRES_API_KEY)
                   for p in available_providers()],
        demo_mode=_quota.limiti.enabled,
        max_questions=_quota.limiti.max_questions if _quota.limiti.enabled else 0,
        max_upload_mb=MAX_UPLOAD_MB,
        supported_extensions=list(SUPPORTED_EXTENSIONS),
        # Le stesse liste che usa l'app Streamlit: erano ribattute nel client.
        project_questions=list(PROJECT_QUESTIONS),
        frequencies=list(FREQUENCIES),
    )


@router.get("/health", summary="Il servizio è vivo")
def health() -> dict[str, object]:
    return {"status": "ok", "datasets_in_memoria": len(store.magazzino)}


# --- Dataset ------------------------------------------------------------------
def _descrivi(df: pd.DataFrame, dataset_id: str, etichetta: str) -> DatasetResponse:
    prof = profile(df)
    misure = ordered_measures(measure_columns(df))
    categorie = category_columns(df)
    # La percentuale di mancanti si ricalcola dal DataFrame invece di essere
    # estratta da `profile()`, che la restituisce già formattata per la lettura
    # ("0 (0%)"). Un'API deve dare un NUMERO: chi la consuma decide come mostrarlo,
    # e nessuno deve parsificare una stringa pensata per un occhio umano.
    colonne = [
        ColumnInfo(name=str(r["Colonna"]), kind=cast(ColumnKind, str(r["Tipo"])),
                   missing_pct=round(float(df[str(r["Colonna"])].isna().mean() * 100), 2),
                   unique=int(r["Valori unici"]), detail=str(r["Dettaglio"]))
        for _, r in prof.iterrows()
    ]
    suggerita = misure[0] if misure else None
    return DatasetResponse(
        dataset_id=dataset_id, label=etichetta, rows=len(df), columns=df.shape[1],
        profile=colonne, measures=misure, categories=categorie,
        suggested_measure=suggerita,
        suggested_category=best_category(df) if categorie else None,
        suggested_unit=default_unit(suggerita),
        example_questions=example_questions(
            suggerita, best_category(df) if categorie else None),
    )


@router.post("/dataset", response_model=DatasetResponse, summary="Carica un file da analizzare")
async def carica(file: Annotated[UploadFile, File()]) -> DatasetResponse:
    dati = await file.read()
    if len(dati) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413,
                            detail=f"File troppo grande: il limite è {MAX_UPLOAD_MB} MB.")
    nome = file.filename or "caricato.csv"
    chiave = store.impronta(dati, nome)

    gia_presente = store.magazzino.prendi(chiave)
    if gia_presente is not None:
        # Stesso contenuto, stessa voce: ricaricare due volte non raddoppia la RAM.
        return _descrivi(gia_presente.df, chiave, gia_presente.etichetta)

    try:
        df = read_any(NamedBytesIO(dati, nome))
    except Exception as e:  # noqa: BLE001 — file dell'utente: si spiega, non si esplode
        raise HTTPException(status_code=400, detail=f"File illeggibile: {e}") from e

    store.magazzino.aggiungi(chiave, df, nome)
    return _descrivi(df, chiave, nome)


@router.post("/dataset/demo", response_model=DatasetResponse,
             summary="Carica il dataset di esempio")
def carica_demo() -> DatasetResponse:
    """Stessa pipeline del file dell'utente: il default non è un caso speciale."""
    df = load_dataset()
    chiave = store.impronta(b"__demo__", "sales.csv")
    store.magazzino.aggiungi(chiave, df, "Dataset di esempio (Superstore Sales)")
    return _descrivi(df, chiave, "Dataset di esempio (Superstore Sales)")


# --- Report -------------------------------------------------------------------
@router.get("/dataset/{dataset_id}/report", response_model=ReportResponse,
            summary="KPI, insight e grafici del report iniziale")
def report(dataset_id: str, measure: str | None = None,
           category: str | None = None, unit: str = "",
           filter_column: str | None = None,
           filter_values: "Annotated[list[str] | None, Query()]" = None) -> ReportResponse:
    voce = _dataset(dataset_id)
    filtro = (FiltroSpec(column=filter_column, values=filter_values)
              if filter_column and filter_values else None)
    df, etichetta_filtro = _filtrato(voce.df, filtro)
    misure = ordered_measures(measure_columns(df))
    measure = measure or (misure[0] if misure else None)
    category = category or best_category(df)
    unit = unit or default_unit(measure)

    insights = analyze(df, measure, category)

    figure: dict[str, object] = {}
    if insights.get("top") is not None:
        fig = charts.try_fig(charts.try_chart, insights["top"].data, kind="bar")
        if fig is not None:
            figure["top"] = _figura_json(fig)
    if insights.get("trend") is not None:
        fig = charts.try_fig(charts.try_chart, insights["trend"].data, kind="line")
        if fig is not None:
            figure["trend"] = _figura_json(fig)
    if measure:
        # Con `try_fig`, come fa Streamlit: una colonna su cui l'istogramma
        # esplode toglie un grafico, non fa cadere l'intera rotta con un 500.
        fig = charts.try_fig(charts.histogram, df, measure)
        if fig is not None:
            figure["dist"] = _figura_json(fig)
    # La mappa di correlazione: il modello dei dati la prometteva gia' e l'API non
    # la produceva mai, mentre Streamlit la disegna. Compare solo quando `analyze`
    # trova almeno due misure e righe a sufficienza (vedi loader._correlations).
    if insights.get("corr") is not None:
        fig = charts.try_fig(charts.corr_heatmap, insights["corr"])
        if fig is not None:
            figure["corr"] = _figura_json(fig)

    stats = insights.get("numeric_stats")
    return ReportResponse(
        measure=measure, category=category, unit=unit, filter_label=etichetta_filtro,
        kpis=[Kpi(label=k[0], value=k[1], sub=k[2], tick=k[3])
              for k in build_kpis(df, measure, category, unit)],
        findings=list(insights.get("findings", [])),
        numeric_stats=_righe_json(stats) if stats is not None else [],
        preview=_righe_json(df, 10),
        figures=figure,
    )


# --- Domande sui dati ---------------------------------------------------------
def _valore_serializzabile(value: object) -> tuple[object, ValueKind]:
    """Traduce il risultato in qualcosa che JSON sa portare, dichiarandone il tipo."""
    if isinstance(value, pd.DataFrame):
        return _righe_json(value), "table"
    if isinstance(value, pd.Series):
        return _righe_json(value.rename("valore").reset_index()), "series"
    if isinstance(value, (int, float)):
        return (None if pd.isna(value) else value), "scalar"
    if value is None:
        return None, "none"
    return str(value), "text"


def _risposta(turn: Turn, colonne, *, includi_spiegazione: bool = True) -> dict:
    """
    Il `Turn` del dominio tradotto nella forma che l'API espone.

    Estratta perche' la usano DUE strade: la risposta unica e quella a pezzi. Se
    ognuna costruisse la propria, le due comincerebbero a differire — ed e' il
    tipo di divergenza che si nota solo quando un client ne usa una e si aspetta
    l'altra.
    """
    if not isinstance(turn.result, ExecutionSuccess):
        return AskResponse(ok=False, question=turn.question, code=turn.code,
                           failure_kind=turn.result.kind,
                           message=turn.result.message,
                           advice=advice_for(turn.result.kind)).model_dump()

    valore, tipo = _valore_serializzabile(turn.result.value)
    return AskResponse(
        ok=True, question=turn.question, code=turn.code,
        answer=turn.explanation if includi_spiegazione else None,
        value=valore, value_kind=tipo,
        figure=_figura_json(turn.result.fig) if turn.result.fig is not None else None,
        columns_used=checks.columns_referenced(turn.code, colonne),
        warnings=checks.sanity_warnings(turn.result.value),
    ).model_dump()


@router.post("/ask", response_model=AskResponse, summary="Fai una domanda sui dati")
def ask(req: AskRequest, request: Request,
        x_api_key: str | None = Header(default=None)) -> AskResponse:
    _consuma_quota(request, x_api_key)
    voce = _dataset(req.dataset_id)
    df, _ = _filtrato(voce.df, req.filtro)
    service = AnalysisService(_agente(req.provider, req.model, x_api_key))
    turn = service.answer(req.question, df, explain=req.explain, unit=req.unit)
    return AskResponse(**_risposta(turn, df.columns))


@router.post("/ask/stream", summary="La stessa domanda, trasmessa mentre accade")
def ask_stream(req: AskRequest, request: Request,
               x_api_key: str | None = Header(default=None)):
    """
    Server-Sent Events: avanzamento, risultato, poi la spiegazione a pezzi.

    Il RISULTATO arriva prima della spiegazione, quindi tabella e grafico
    compaiono appena esistono invece di aspettare la prosa — che e' la parte
    lenta. Dettagli del protocollo in `nlda.api.streaming`.
    """
    voce = _dataset(req.dataset_id)
    df, _ = _filtrato(voce.df, req.filtro)
    service = AnalysisService(_agente(req.provider, req.model, x_api_key))

    # Si cattura l'ELENCO delle colonne, non il DataFrame: alla lambda serve solo
    # quello, e catturare `df` lo terrebbe in vita per tutta la durata dello
    # stream — che e' la parte lunga del turno.
    colonne = df.columns
    eventi = trasmetti(
        service, req.question, df, unit=req.unit,
        verso_json=lambda turn, includi_spiegazione: _risposta(
            turn, colonne, includi_spiegazione=includi_spiegazione),
    )
    return StreamingResponse(
        eventi,
        media_type="text/event-stream",
        # Senza, un proxy puo' bufferizzare la risposta e consegnarla tutta
        # insieme alla fine: lo streaming resterebbe tale solo sulla carta.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _testo_insight(dataset_id: str, measure: str | None,
                   category: str | None, unit: str) -> str | None:
    """
    I numeri gia' calcolati, in forma di testo, pronti per il prompt.

    E' l'ingresso comune di sintesi e report esecutivo: due prodotti diversi
    scritti a partire dagli STESSI numeri. Tenerlo in un posto solo garantisce
    che non possano divergere — se domani la scelta della misura predefinita
    cambia, cambia per entrambi.

    Restituisce `None` quando il dataset non offre nulla da riassumere; cosa
    farne (una sintesi vuota o un errore) lo decide la rotta.
    """
    df = _dataset(dataset_id).df
    misure = ordered_measures(measure_columns(df))
    measure = measure or (misure[0] if misure else None)
    testo = analyze(df, measure, category or best_category(df)).get("text")
    return with_unit(testo, unit or default_unit(measure)) if testo else None


@router.post("/dataset/{dataset_id}/overview", response_model=OverviewResponse,
             summary="La sintesi in prosa del report")
def overview(dataset_id: str, request: Request, measure: str | None = None,
             category: str | None = None, unit: str = "",
             x_api_key: str | None = Header(default=None),
             provider: str | None = None, model: str | None = None) -> OverviewResponse:
    """
    Il pezzo che il report React non aveva e quello Streamlit si', cioe' la
    divergenza piu' grande fra le due interfacce.

    E' una rotta A PARTE e non un campo del report per lo stesso motivo per cui in
    Streamlit si genera per ultima: e' l'unica parte che aspetta il modello — con
    un modello locale sono decine di secondi — e infilarla nel report farebbe
    aspettare anche i numeri, che sono gia' pronti.

    I numeri li ha gia' calcolati Pandas: al modello arriva `insights["text"]`, e
    il prompt gli vieta di calcolarne altri.
    """
    # Prima si guarda se c'e' qualcosa da riassumere, poi si scala la quota: una
    # sintesi che non parte non deve costare al visitatore una domanda.
    testo = _testo_insight(dataset_id, measure, category, unit)
    if testo is None:
        return OverviewResponse(text=None)
    _consuma_quota(request, x_api_key)
    return OverviewResponse(text=_agente(provider, model, x_api_key).overview(testo))


@router.post("/dataset/{dataset_id}/executive-report", response_model=ExecutiveReportResponse,
             summary="Il report esecutivo in Markdown")
def executive_report(dataset_id: str, request: Request, measure: str | None = None,
                     category: str | None = None, unit: str = "",
                     x_api_key: str | None = Header(default=None),
                     provider: str | None = None,
                     model: str | None = None) -> ExecutiveReportResponse:
    """
    Cinque sezioni (Executive Summary, Key Insights, Business Recommendations,
    Possible Risks, Next Steps) scritte dal modello sui numeri gia' calcolati.

    Come la sintesi, e' una rotta a parte perche' aspetta il modello — e a
    differenza della sintesi non si genera da sola: la si chiede, perche' costa
    una chiamata e non a tutti serve.

    Il prompt impone di usare SOLO i numeri forniti e di formulare
    raccomandazioni e rischi come ipotesi: correlazione non e' causa.
    """
    testo = _testo_insight(dataset_id, measure, category, unit)
    if testo is None:
        # Stessa condizione con cui Streamlit disabilita il pulsante
        # (`pages.render_executive_report`). Oggi `analyze` un testo lo produce
        # sempre, anche su zero righe, quindi in pratica non si arriva qui: la
        # guardia resta perche' la garanzia e' di `analyze`, non di questa rotta,
        # e senza il modello riceverebbe un prompt vuoto.
        raise HTTPException(status_code=400,
                            detail="Questo dataset non offre nulla da riassumere.")
    _consuma_quota(request, x_api_key)
    return ExecutiveReportResponse(
        markdown=_agente(provider, model, x_api_key).executive_report(testo))


# --- Domande sul progetto -----------------------------------------------------
@router.post("/project-qa", response_model=ProjectQaResponse,
             summary="Fai una domanda sul progetto, con fonti")
def project_qa(req: ProjectQaRequest, request: Request,
               x_api_key: str | None = Header(default=None)) -> ProjectQaResponse:
    # Stessa quota della chat sui dati: e' la stessa spesa, sullo stesso credito.
    _consuma_quota(request, x_api_key)
    nome, modello = _scelta_modello(req.provider, req.model)
    provider = get_provider(nome, model_name=modello, api_key=x_api_key)
    testo, fonti = project_answer(provider, req.question)
    return ProjectQaResponse(answer=testo, sources=[f.citazione for f in fonti])


# --- Filtro, periodi, unione, esportazione ------------------------------------
@router.get("/dataset/{dataset_id}/distinct", response_model=DistinctResponse,
            summary="Valori distinti di una colonna, per costruire un filtro")
def distinct(dataset_id: str, column: str) -> DistinctResponse:
    voce = _dataset(dataset_id)
    _esigi_colonne(voce.df, column=column)
    # Ordinati e come STRINGHE: il filtro confronta su stringa (views.apply_filter),
    # quindi cio' che si mostra e cio' che si confronta sono la stessa cosa.
    valori = sorted(voce.df[column].dropna().astype(str).unique())
    return DistinctResponse(column=column, values=valori[:MAX_VALORI_DISTINTI],
                            truncated=len(valori) > MAX_VALORI_DISTINTI)


@router.get("/dataset/{dataset_id}/date-columns", response_model=list[str],
            summary="Colonne utilizzabili come asse temporale")
def date_columns_route(dataset_id: str) -> list[str]:
    """La decisione sta in `loader.date_columns`: la stessa che usa Streamlit."""
    return date_columns(_dataset(dataset_id).df)


@router.get("/dataset/{dataset_id}/periods", response_model=PeriodsResponse,
            summary="Confronto di una misura tra periodi consecutivi")
def periods(dataset_id: str, date_column: str, measure: str,
            freq: str = "trimestre") -> PeriodsResponse:
    """
    Il calcolo NON e' rifatto qui: `nlda.periods.compare_periods` e' lo stesso
    motore che la sandbox espone al codice generato e che l'app Streamlit chiama
    direttamente. Tre strade, una implementazione.
    """
    voce = _dataset(dataset_id)
    _esigi_colonne(voce.df, date_column=date_column, measure=measure)
    try:
        tabella = compare_periods(voce.df, date_column, measure, freq=freq)
    except ValueError as e:
        # Frequenza ignota o colonna senza date riconoscibili: e' un errore di
        # CHIAMATA, e il messaggio del motore dice gia' cosa non va.
        raise HTTPException(status_code=400, detail=str(e)) from e

    righe = [
        PeriodRow(period=str(r["periodo"]),
                  value=None if pd.isna(r[measure]) else float(r[measure]),
                  change_pct=None if pd.isna(r["variazione_%"]) else float(r["variazione_%"]))
        for _, r in tabella.iterrows()
    ]
    return PeriodsResponse(rows=righe, measure=measure, freq=freq)


@router.post("/dataset/join", response_model=DatasetResponse,
             summary="Unisce due dataset gia' caricati")
def join(req: JoinRequest) -> DatasetResponse:
    sinistra, destra = _dataset(req.left_id), _dataset(req.right_id)
    _esigi_colonne(sinistra.df, left_on=req.left_on)
    _esigi_colonne(destra.df, right_on=req.right_on)
    try:
        unito = join_datasets(sinistra.df, destra.df, req.left_on, req.right_on, how=req.how)
    except Exception as e:  # noqa: BLE001 - chiavi incompatibili: si spiega
        raise HTTPException(status_code=400, detail=f"Unione non riuscita: {e}") from e

    etichetta = f"{sinistra.etichetta} + {destra.etichetta}"
    # L'identificativo deriva dai due di partenza e dai parametri: rifare la stessa
    # unione ridà la stessa voce invece di duplicarla in memoria.
    chiave = store.impronta(
        f"{req.left_id}|{req.right_id}|{req.left_on}|{req.right_on}|{req.how}".encode())
    store.magazzino.aggiungi(chiave, unito, etichetta)
    return _descrivi(unito, chiave, etichetta)


@router.post("/export", response_model=ExportResponse,
             summary="La conversazione in Markdown")
def export(req: ExportRequest) -> ExportResponse:
    """
    Il Markdown lo compone `nlda.export`, lo stesso modulo che serve l'app
    Streamlit: e' l'unico posto in cui si decide come si impagina un turno, e
    rifarlo nel client significherebbe due formati destinati a divergere.

    I turni arrivano DAL CLIENT perche' l'API non tiene la conversazione (nessuna
    sessione, vedi il docstring del modulo). E' anche il motivo per cui qui si
    ricostruiscono `Turn` minimi: del risultato serve la rappresentazione
    testuale, non l'oggetto Pandas originale, che il client non ha mai avuto.
    """
    turni = [
        Turn(question=t.question, code=t.code,
             result=ExecutionSuccess(fig=None, value=t.value_preview or None, summary=""),
             explanation=t.answer)
        for t in req.turns
    ]
    return ExportResponse(
        markdown=conversation_to_markdown(turni, dataset_label=req.dataset_label))


def create_app() -> FastAPI:
    """
    Costruisce l'applicazione. È una funzione e non un modulo-livello perché i test
    ne vogliono una istanza pulita, e perché il montaggio del frontend statico (che
    arriverà) dipende da cosa è stato costruito.
    """
    app = FastAPI(
        title="Natural Language Data Analyst — API",
        version="1.1.0",
        summary="Interroga i tuoi dati in linguaggio naturale: l'LLM racconta, Pandas calcola.",
    )

    @app.exception_handler(HTTPException)
    async def _errore(_request, exc: HTTPException):  # noqa: ANN202
        """Una sola forma d'errore per tutta l'API: il client ne gestisce una."""
        return JSONResponse(status_code=exc.status_code,
                            content=ErrorResponse(detail=str(exc.detail)).model_dump())

    app.include_router(router)
    _monta_frontend(app)
    log.info("api_pronta", extra={"timeout_exec": settings.exec_timeout})
    return app


# Il build di Vite. Sta fuori dal pacchetto Python perché è un artefatto di
# compilazione, non sorgente: in sviluppo non esiste affatto (ci pensa il server
# di Vite col suo proxy), e in produzione lo si costruisce prima di avviare.
_STATICI = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def _monta_frontend(app: FastAPI) -> None:
    """
    Serve il frontend compilato dalla stessa applicazione che serve l'API.

    Perché un solo servizio invece di due: con un'origine sola non serve CORS, non
    ci sono due deploy da tenere allineati, e l'indirizzo dell'API nel client resta
    un percorso relativo — quindi nessuna variabile d'ambiente da configurare per
    dirgli dove sta il backend.

    Il montaggio avviene DOPO le rotte dell'API: la radice `/` cattura tutto ciò
    che resta, e se venisse prima ingoierebbe anche `/api/...`.

    Se `dist/` non esiste — sviluppo, o un'immagine costruita senza il passo di
    build — non si monta nulla e non si solleva niente: l'API resta perfettamente
    utilizzabile, ed è il modo in cui girano i test.
    """
    if not (_STATICI / "index.html").exists():
        log.info("frontend_assente", extra={"percorso": str(_STATICI)})
        return

    # `html=True` serve `index.html` quando si chiede la RADICE, che è ciò che
    # serve qui. Non è un ripiego universale: un percorso inventato riceve 404, e
    # va bene — questa applicazione è una pagina sola, senza instradamento lato
    # client, quindi non esistono altri indirizzi legittimi da recuperare. Il
    # giorno in cui si aggiungessero delle rotte nel browser servirebbe una rotta
    # jolly che rimanda a `index.html`; oggi sarebbe codice per un caso che non
    # esiste, e maschererebbe i 404 veri.
    app.mount("/", StaticFiles(directory=_STATICI, html=True), name="frontend")
    log.info("frontend_montato", extra={"percorso": str(_STATICI)})


app = create_app()
