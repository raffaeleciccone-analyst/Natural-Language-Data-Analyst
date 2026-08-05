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
import threading
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

from nlda import __version__, charts, checks
from nlda.agent import DataAgent
from nlda.api import quota, store
from nlda.api.models import (
    AskRequest,
    AskResponse,
    ColumnInfo,
    ColumnKind,
    ConfigResponse,
    DatasetResponse,
    DemoDataset,
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
from nlda.demo_data import DatasetDemo
from nlda.demo_data import disponibili as demo_disponibili
from nlda.demo_data import trova as trova_demo
from nlda.export import conversation_to_markdown
from nlda.kpis import build_kpis
from nlda.loader import (
    SUPPORTED_EXTENSIONS,
    NamedBytesIO,
    analyze,
    best_category,
    category_columns,
    date_columns,
    date_span_years,
    default_unit,
    duplicate_columns_warning,
    load_dataset,
    measure_columns,
    ordered_measures,
    profile,
    read_any,
    stima_ram,
)
from nlda.log import get_logger
from nlda.periods import compare_periods
from nlda.project_qa import answer as project_answer
from nlda.providers import DEFAULT_MODELS, REQUIRES_API_KEY, available_providers, get_provider
from nlda.results import ExecutionSuccess, advice_for
from nlda.sandbox.pool import riserva
from nlda.service import AnalysisService, Turn
from nlda.suggestions import FREQUENCIES, PROJECT_QUESTIONS, example_questions
from nlda.utils import with_unit
from nlda.views import apply_filter, join_datasets, join_warning

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

# Lo stesso tetto della UI: un limite per interfaccia sarebbe una bugia. Vive in
# `settings` perché un deploy deve poterlo cambiare senza toccare il codice —
# `.streamlit/config.toml` lo fa da sempre, l'API lo teneva scritto qui dentro.
MAX_UPLOAD_MB = settings.max_upload_mb

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
    colonna = _colonna(df, "filter_column", filtro.column)
    return apply_filter(df, (colonna, tuple(filtro.values)))


def _colonna(df: pd.DataFrame, parametro: str, nome: str) -> str:
    """
    Il nome di colonna cosi' come lo scrive il dataset, o 400 se non esiste —
    con lo STESSO messaggio ovunque. Il nome del parametro fa da etichetta, cosi'
    chi legge l'errore sa quale campo della richiesta correggere.

    Prima il controllo era ripetuto in quattro rotte con tre messaggi diversi: chi
    integra l'API imparava a riconoscerne uno e trovava gli altri.

    Gli spazi ai bordi si tollerano solo come RIPIEGO — `?measure=Vendite `
    capita a chi compone l'URL a mano o incolla un'etichetta — e mai a scapito
    del dataset: se una colonna si chiama davvero `'Vendite '`, vince quella. Per
    questo il nome RISOLTO si restituisce: chi poi indicizza il DataFrame deve
    usare questo, non quello arrivato nella richiesta.
    """
    if nome in df.columns:
        return nome
    senza_spazi = nome.strip()
    if senza_spazi and senza_spazi in df.columns:
        return senza_spazi
    raise HTTPException(
        status_code=400,
        detail=f"{parametro}: la colonna '{nome}' non esiste in questo dataset.")


def _esigi_misura(df: pd.DataFrame, parametro: str, nome: str) -> str:
    """
    Come `_colonna`, ma per una misura: esistere non basta, deve contenere numeri.

    `build_kpis` ne fa una media e `compare_periods` una divisione: su una colonna
    di testo Pandas solleva `TypeError`, che diventava un **500** — cioe' "il
    servizio e' guasto" — per quello che e' un errore di CHIAMATA. Stessa
    distinzione del resto del modulo: 4xx a chi ha sbagliato la richiesta.

    Le misure vere l'API le ha gia' dichiarate caricando il dataset (`measures`
    nella risposta di `/api/dataset`); il messaggio le ripete perche' chi sbaglia
    il parametro non debba andare a ripescarle.
    """
    nome = _colonna(df, parametro, nome)
    if not pd.api.types.is_numeric_dtype(df[nome]):
        disponibili = ordered_measures(measure_columns(df))
        elenco = ", ".join(f"'{m}'" for m in disponibili) if disponibili else "nessuna"
        raise HTTPException(
            status_code=400,
            detail=f"{parametro}: la colonna '{nome}' non contiene numeri, quindi non "
                   f"puo' fare da misura. Misure disponibili: {elenco}.")
    return nome


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
        max_dataset_ram_mb=settings.max_dataset_ram_mb,
        supported_extensions=list(SUPPORTED_EXTENSIONS),
        # Le stesse liste che usa l'app Streamlit: erano ribattute nel client.
        project_questions=list(PROJECT_QUESTIONS),
        frequencies=list(FREQUENCIES),
        demo_datasets=[DemoDataset(name=d.nome, label=d.etichetta, description=d.descrizione)
                       for d in demo_disponibili()],
    )


@router.get("/health", summary="Il servizio è vivo")
def health() -> dict[str, object]:
    return {"status": "ok", "datasets_in_memoria": len(store.magazzino)}


# --- Dataset ------------------------------------------------------------------
def _descrivi(df: pd.DataFrame, dataset_id: str, etichetta: str,
              avvisi: "list[str] | None" = None) -> DatasetResponse:
    prof = profile(df)
    misure = ordered_measures(measure_columns(df))
    categorie = category_columns(df)
    # Data e seconda misura servono alle domande d'esempio, per proporre cio' che
    # il report NON risponde gia': il massimo nel tempo, il legame fra due misure.
    date_disponibili = date_columns(df)
    date_column = date_disponibili[0] if date_disponibili else None
    span = date_span_years(df, date_column)
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
        # Data e seconda misura servono a proporre domande che il report NON
        # risponde gia': il massimo nel tempo, il legame fra due misure.
        example_questions=example_questions(
            suggerita, best_category(df) if categorie else None,
            date_column=date_column, date_span_years=span,
            other_measures=misure),
        warnings=avvisi or [],
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

    # Si fa posto PRIMA di leggere. Durante la lettura i dataset già in memoria
    # occupano ancora tutto il loro spazio, e il picco del parser si somma al
    # loro: sulla demo, con 40 MB in magazzino, caricarne altri 40 uccideva il
    # container. Quando la stima non è possibile (Excel, JSON) si prenota il
    # massimo che un dataset può occupare: si sfratta di troppo, non di meno.
    #
    # La stima si paga due volte, qui e dentro il caricatore: 41 ms su un file da
    # 20 MB, contro i 522 della lettura. Si è preferito il costo alla scorciatoia
    # di prenotare sempre il massimo, che sfratterebbe dataset ancora utili a
    # ogni caricamento — anche per un file da 100 KB.
    store.magazzino.fai_spazio(stima_ram(dati, nome)
                               or settings.max_dataset_ram_mb * 1024 * 1024)
    try:
        df = read_any(NamedBytesIO(dati, nome))
    except ValueError as e:
        # I nostri messaggi sono già scritti per chi carica il file (in italiano, e
        # dicono cosa manca): ripeterli dietro un "File illeggibile:" li seppellirebbe
        # sotto un'etichetta generica.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — file dell'utente: si spiega, non si esplode
        raise HTTPException(status_code=400, detail=f"File illeggibile: {e}") from e

    store.magazzino.aggiungi(chiave, df, nome)
    # Due colonne con lo stesso nome: pandas rinomina la seconda in silenzio, e
    # chi ha caricato il file si ritrova due misure dove ne aveva una. Stesso
    # canale dell'avviso sull'unione — un dataset può arrivare con qualcosa da
    # dire, e il posto per dirlo esiste già.
    avviso = duplicate_columns_warning(dati, nome)
    return _descrivi(df, chiave, nome, [avviso] if avviso else [])


def _demo_in_memoria(scelto: DatasetDemo) -> tuple[pd.DataFrame, str, str]:
    """
    Il dataset di esempio, letto da disco UNA volta sola.

    Prima si rileggeva e ri-analizzava il file a ogni visita, anche se era
    identico e gia' in memoria: misurato sul deploy, 9,5 secondi per ogni utente
    che apriva la pagina — piu' di sette volte il report che ne segue. Il file di
    esempio non cambia, quindi la seconda lettura non poteva dare niente di
    diverso dalla prima.

    L'impronta include il NOME del file: senza, due esempi diversi finirebbero
    sulla stessa chiave e il secondo restituirebbe il primo.
    """
    chiave = store.impronta(b"__demo__", scelto.file)
    etichetta = f"Esempio · {scelto.etichetta}"
    voce = store.magazzino.prendi(chiave)
    if voce is not None:
        return voce.df, chiave, etichetta

    df = load_dataset(scelto.file)
    store.magazzino.aggiungi(chiave, df, etichetta)
    return df, chiave, etichetta


@router.post("/dataset/demo", response_model=DatasetResponse,
             summary="Carica il dataset di esempio")
def carica_demo(nome: str | None = None) -> DatasetResponse:
    """
    Stessa pipeline del file dell'utente: il default non è un caso speciale.

    Senza `nome` si prende il primo disponibile, così il pulsante "usa il dataset
    di esempio" continua a funzionare come prima.
    """
    scelto = trova_demo(nome)
    if scelto is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset di esempio '{nome}' non disponibile in questa installazione."
            if nome else "Questa installazione non ha dataset di esempio.")

    return _descrivi(*_demo_in_memoria(scelto))


# --- Report -------------------------------------------------------------------
def _classifica(insights: dict, df_intero: pd.DataFrame, filtro: "FiltroSpec | None",
                measure: str | None, category: str | None):
    """
    L'aggregato della classifica e i valori da evidenziare.

    E' l'unico grafico che il filtro sulla PROPRIA categoria distrugge: restava
    una barra sola, larga quanto il pannello, e una classifica di un elemento non
    e' una classifica. Si ricalcola quindi sui dati non filtrati e si evidenzia
    la selezione — il confronto con le altre categorie e' l'informazione, e
    filtrandolo si perde.

    Gli altri grafici no: l'andamento e la distribuzione DEL sottoinsieme sono
    esattamente cio' che si e' chiesto filtrando.
    """
    if not (filtro and category and filtro.column == category):
        return insights["top"], None
    fuori_filtro = analyze(df_intero, measure, category).get("top")
    if fuori_filtro is None:
        return insights["top"], None
    return fuori_filtro, filtro.values


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
    # I parametri si validano PRIMA di calcolare: `analyze` tollera una misura
    # sbagliata, `build_kpis` no — e senza questo controllo il 500 arrivava da
    # li', a report quasi finito.
    measure = _esigi_misura(df, "measure", measure) if measure else None
    category = _colonna(df, "category", category) if category else None
    misure = ordered_measures(measure_columns(df))
    measure = measure or (misure[0] if misure else None)
    category = category or best_category(df)
    unit = unit or default_unit(measure)

    insights = analyze(df, measure, category)

    figure: dict[str, object] = {}

    def disegna(nome: str, fn, *args, **kwargs) -> None:
        """
        Aggiunge una figura, se si e' potuta costruire.

        `try_fig` fa come Streamlit: una colonna su cui l'istogramma esplode
        toglie UN grafico, non fa cadere l'intera rotta con un 500. Questo
        raccoglie quel "se e' venuta" che stava scritto quattro volte.
        """
        fig = charts.try_fig(fn, *args, **kwargs)
        if fig is not None:
            figure[nome] = _figura_json(fig)

    if insights.get("top") is not None:
        classifica, evidenziate = _classifica(insights, voce.df, filtro, measure, category)
        disegna("top", charts.try_chart, classifica.data, kind="bar", evidenzia=evidenziate)
    if insights.get("trend") is not None:
        disegna("trend", charts.try_chart, insights["trend"].data, kind="line")
    if measure:
        disegna("dist", charts.histogram, df, measure)
    # La mappa di correlazione compare solo quando `analyze` trova almeno due
    # misure e righe a sufficienza (vedi `loader._correlations`).
    if insights.get("corr") is not None:
        disegna("corr", charts.corr_heatmap, insights["corr"])

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
    # Avviso anti-allucinazione: se la domanda nomina una colonna inesistente, il
    # modello tende a sostituirla in silenzio con una reale e a spacciarla per quella
    # chiesta. Si calcola PRIMA del bivio successo/fallimento perché vale in entrambi:
    # la colonna sostituita può far riuscire il codice tanto quanto farlo fallire. È
    # lo stesso avviso che l'app Streamlit mostra — ora prodotto una volta sola, in
    # `checks`, così le due interfacce non possono divergere.
    # Include anche la verifica della mappa termine→colonna dichiarata dal modello
    # (regola 10 del prompt): «profitto» su un dataset che non ce l'ha.
    avvisi_domanda = checks.question_warnings(turn.question, turn.code, colonne)

    if not isinstance(turn.result, ExecutionSuccess):
        return AskResponse(ok=False, question=turn.question, code=turn.code,
                           failure_kind=turn.result.kind,
                           message=turn.result.message,
                           advice=advice_for(turn.result.kind),
                           warnings=avvisi_domanda).model_dump()

    valore, tipo = _valore_serializzabile(turn.result.value)
    return AskResponse(
        ok=True, question=turn.question, code=turn.code,
        answer=turn.explanation if includi_spiegazione else None,
        value=valore, value_kind=tipo,
        figure=_figura_json(turn.result.fig) if turn.result.fig is not None else None,
        columns_used=checks.columns_referenced(turn.code, colonne),
        warnings=avvisi_domanda + checks.sanity_warnings(turn.result.value),
    ).model_dump()


@router.post("/ask", response_model=AskResponse, summary="Fai una domanda sui dati")
def ask(req: AskRequest, request: Request,
        x_api_key: str | None = Header(default=None)) -> AskResponse:
    # Prima si guarda se il dataset c'e', poi si scala la quota. Al contrario, chi
    # torna su una scheda lasciata aperta e trova il dataset scaduto pagava il
    # 404 con una domanda del suo budget: gli si toglieva una risposta senza
    # dargliene una. Lo stesso ordine vale in `ask_stream`.
    voce = _dataset(req.dataset_id)
    _consuma_quota(request, x_api_key)
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

    La quota si scala QUI, nel corpo della rotta, non dentro il generatore: un
    429 deve essere una risposta HTTP con il suo messaggio, non un evento
    `error` dentro un flusso gia' aperto con stato 200.

    `explain=false` chiude il flusso dopo il risultato. La rotta accettava il
    campo e lo ignorava: chi chiedeva i soli numeri pagava lo stesso la seconda
    chiamata al modello — e sulla demo la paga il budget di tutti.
    """
    voce = _dataset(req.dataset_id)
    _consuma_quota(request, x_api_key)
    df, _ = _filtrato(voce.df, req.filtro)
    service = AnalysisService(_agente(req.provider, req.model, x_api_key))

    # Si cattura l'ELENCO delle colonne, non il DataFrame: alla lambda serve solo
    # quello, e catturare `df` lo terrebbe in vita per tutta la durata dello
    # stream — che e' la parte lunga del turno.
    colonne = df.columns
    eventi = trasmetti(
        service, req.question, df, unit=req.unit, explain=req.explain,
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
    # Anche qui prima di tutto: una misura sbagliata deve costare un 400, non una
    # chiamata al modello pagata per riassumere numeri che non sono stati chiesti.
    measure = _esigi_misura(df, "measure", measure) if measure else None
    category = _colonna(df, "category", category) if category else None
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
    column = _colonna(voce.df, "column", column)
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
    date_column = _colonna(voce.df, "date_column", date_column)
    measure = _esigi_misura(voce.df, "measure", measure)
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
    left_on = _colonna(sinistra.df, "left_on", req.left_on)
    right_on = _colonna(destra.df, "right_on", req.right_on)
    try:
        unito = join_datasets(sinistra.df, destra.df, left_on, right_on, how=req.how)
    except Exception as e:  # noqa: BLE001 - chiavi incompatibili: si spiega
        raise HTTPException(status_code=400, detail=f"Unione non riuscita: {e}") from e

    etichetta = f"{sinistra.etichetta} + {destra.etichetta}"
    # L'identificativo deriva dai due di partenza e dai parametri RISOLTI: rifare
    # la stessa unione ridà la stessa voce invece di duplicarla in memoria, anche
    # se la seconda richiesta ha scritto la chiave con uno spazio in coda.
    chiave = store.impronta(
        f"{req.left_id}|{req.right_id}|{left_on}|{right_on}|{req.how}".encode())
    store.magazzino.aggiungi(chiave, unito, etichetta)
    # Un'unione che moltiplica le righe non fallisce: gonfia i totali in silenzio.
    # Stesso avviso che mostra Streamlit, composto in `views`: una voce sola.
    avviso = join_warning(sinistra.df, destra.df, unito, left_on, right_on)
    return _descrivi(unito, chiave, etichetta, [avviso] if avviso else [])


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


def _scalda_demo() -> None:
    """Mette in memoria il primo dataset di esempio. Un guasto qui non e' fatale:
    la rotta lo rileggerebbe da disco, solo piu' lentamente."""
    try:
        scelti = demo_disponibili()
        if scelti:
            _demo_in_memoria(scelti[0])
            log.info("demo_preriscaldata", extra={"dataset": scelti[0].nome})
    except Exception as e:  # noqa: BLE001 — preriscaldamento: mai fatale
        log.warning("Preriscaldamento del dataset di esempio non riuscito: %s", e)


def create_app() -> FastAPI:
    """
    Costruisce l'applicazione. È una funzione e non un modulo-livello perché i test
    ne vogliono una istanza pulita, e perché il montaggio del frontend statico (che
    arriverà) dipende da cosa è stato costruito.
    """
    app = FastAPI(
        title="Natural Language Data Analyst — API",
        # Dal pacchetto, non ricopiata: la versione che l'API dichiara nello
        # schema OpenAPI è la stessa che il progetto distribuisce.
        version=__version__,
        summary="Interroga i tuoi dati in linguaggio naturale: l'LLM racconta, Pandas calcola.",
    )

    @app.exception_handler(HTTPException)
    async def _errore(_request, exc: HTTPException):  # noqa: ANN202
        """Una sola forma d'errore per tutta l'API: il client ne gestisce una."""
        return JSONResponse(status_code=exc.status_code,
                            content=ErrorResponse(detail=str(exc.detail)).model_dump())

    app.include_router(router)
    _monta_frontend(app)

    # Un worker si scalda all'avvio, poi dopo ogni esecuzione (`pool.esegui`).
    # L'app Streamlit lo faceva da sempre, questa no: pagava ~2 secondi di import
    # (pandas, plotly) DENTRO il budget di `EXEC_TIMEOUT`, cioè dentro il tempo
    # concesso al codice generato. Su una macchina scarica passava inosservato;
    # con la CPU occupata da un modello locale bastava a far scadere una
    # `groupby` da tre righe, e l'errore diceva "codice troppo lento" — accusando
    # il codice di una lentezza che era dell'infrastruttura.
    riserva.prewarm()

    # E il dataset di esempio si legge mentre nessuno guarda. Sul deploy la prima
    # lettura costa ~20 secondi (0,1 vCPU, 2 MB di CSV da analizzare colonna per
    # colonna): pagarli qui significa che il primo visitatore trova la pagina
    # pronta invece di essere lui a pagarli. In un thread, perche' l'avvio non
    # deve aspettare: chi arriva prima che finisca semplicemente la legge da
    # disco come faceva prima.
    threading.Thread(target=_scalda_demo, daemon=True, name="nlda-demo").start()

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
