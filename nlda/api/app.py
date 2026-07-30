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
import io
import json
from typing import Annotated, cast

import pandas as pd
from fastapi import APIRouter, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from nlda import charts, checks
from nlda.agent import DataAgent
from nlda.api import store
from nlda.api.models import (
    AskRequest,
    AskResponse,
    ColumnInfo,
    ColumnKind,
    ConfigResponse,
    DatasetResponse,
    ErrorResponse,
    Kpi,
    ProjectQaRequest,
    ProjectQaResponse,
    ProviderInfo,
    ReportResponse,
    ValueKind,
)
from nlda.config import settings
from nlda.loader import (
    SUPPORTED_EXTENSIONS,
    analyze,
    best_category,
    category_columns,
    default_unit,
    load_dataset,
    measure_columns,
    ordered_measures,
    profile,
    read_any,
)
from nlda.log import get_logger
from nlda.project_qa import answer as project_answer
from nlda.providers import DEFAULT_MODELS, REQUIRES_API_KEY, available_providers, get_provider
from nlda.results import ExecutionSuccess
from nlda.service import AnalysisService
from nlda.ui_components import build_kpis

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


class _FileConNome(io.BytesIO):
    """`read_any` riconosce il formato dall'estensione in `.name`; BytesIO non ce l'ha."""

    def __init__(self, dati: bytes, nome: str):
        super().__init__(dati)
        self.name = nome


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
    nome = (provider or _provider_predefinito()).lower()
    if nome not in DEFAULT_MODELS:
        raise HTTPException(status_code=400, detail=f"Provider sconosciuto: '{nome}'.")
    return DataAgent(provider=nome, model_name=model or None, api_key=api_key or None)


def _provider_predefinito() -> str:
    disponibili = available_providers()
    return "ollama" if "ollama" in disponibili else disponibili[0]


# --- Configurazione -----------------------------------------------------------
@router.get("/config", response_model=ConfigResponse, summary="Cosa permette questa installazione")
def config() -> ConfigResponse:
    return ConfigResponse(
        providers=[ProviderInfo(name=p, default_model=DEFAULT_MODELS[p],
                                requires_api_key=p in REQUIRES_API_KEY)
                   for p in available_providers()],
        demo_mode=False,
        max_questions=0,
        max_upload_mb=MAX_UPLOAD_MB,
        supported_extensions=list(SUPPORTED_EXTENSIONS),
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
        df = read_any(_FileConNome(dati, nome))
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
           category: str | None = None, unit: str = "") -> ReportResponse:
    voce = _dataset(dataset_id)
    df = voce.df
    misure = ordered_measures(measure_columns(df))
    measure = measure or (misure[0] if misure else None)
    category = category or best_category(df)
    unit = unit or default_unit(measure)

    insights = analyze(df, measure, category)

    figure: dict[str, object] = {}
    if insights.get("top") is not None:
        fig = charts.try_chart(insights["top"].data, kind="bar")
        if fig is not None:
            figure["top"] = _figura_json(fig)
    if insights.get("trend") is not None:
        fig = charts.try_chart(insights["trend"].data, kind="line")
        if fig is not None:
            figure["trend"] = _figura_json(fig)
    if measure:
        fig = charts.histogram(df, measure)
        if fig is not None:
            figure["dist"] = _figura_json(fig)

    stats = insights.get("numeric_stats")
    return ReportResponse(
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


@router.post("/ask", response_model=AskResponse, summary="Fai una domanda sui dati")
def ask(req: AskRequest, x_api_key: str | None = Header(default=None)) -> AskResponse:
    voce = _dataset(req.dataset_id)
    service = AnalysisService(_agente(req.provider, req.model, x_api_key))
    turn = service.answer(req.question, voce.df, explain=req.explain, unit=req.unit)

    if not isinstance(turn.result, ExecutionSuccess):
        return AskResponse(ok=False, question=turn.question, code=turn.code,
                           failure_kind=turn.result.kind, message=turn.result.message)

    valore, tipo = _valore_serializzabile(turn.result.value)
    return AskResponse(
        ok=True, question=turn.question, code=turn.code, answer=turn.explanation,
        value=valore, value_kind=tipo,
        figure=_figura_json(turn.result.fig) if turn.result.fig is not None else None,
        columns_used=checks.columns_referenced(turn.code, voce.df.columns),
        warnings=checks.sanity_warnings(turn.result.value),
    )


# --- Domande sul progetto -----------------------------------------------------
@router.post("/project-qa", response_model=ProjectQaResponse,
             summary="Fai una domanda sul progetto, con fonti")
def project_qa(req: ProjectQaRequest,
               x_api_key: str | None = Header(default=None)) -> ProjectQaResponse:
    provider = get_provider(req.provider or _provider_predefinito(),
                            model_name=req.model, api_key=x_api_key)
    testo, fonti = project_answer(provider, req.question)
    return ProjectQaResponse(answer=testo, sources=[f.citazione for f in fonti])


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
    log.info("api_pronta", extra={"timeout_exec": settings.exec_timeout})
    return app


app = create_app()
