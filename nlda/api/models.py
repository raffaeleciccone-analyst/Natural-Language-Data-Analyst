"""
Le forme dei dati che l'API scambia.

Non sono solo validazione: da questi modelli FastAPI genera lo schema OpenAPI, e
dallo schema si generano i tipi TypeScript del frontend. Cambiare un campo qui e
dimenticarsene di là smette di essere possibile — il compilatore se ne accorge.
E' il motivo per cui vale la pena descrivere ogni campo invece di passare `dict`.

Convenzione: i nomi dei campi sono in inglese, come il resto delle interfacce
pubbliche del progetto; la prosa resta in italiano.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field

# Tipi nominati invece di Literal ripetuti: `app.py` li usa per dichiarare cio' che
# produce, e mypy verifica che i due lati concordino davvero. Ripetere il Literal
# in due punti significa che possono divergere senza che nessuno se ne accorga.
ColumnKind = Literal["numerica", "testo", "data", "booleana"]
ValueKind = Literal["scalar", "table", "series", "text", "none"]


# --- Configurazione -----------------------------------------------------------
class ProviderInfo(BaseModel):
    """Un provider LLM disponibile in questa installazione."""

    name: str = Field(description="Identificativo da passare alle richieste, es. 'groq'")
    default_model: str = Field(description="Modello usato se il client non ne indica uno")
    requires_api_key: bool = Field(description="Se serve una chiave per usarlo")


class DemoDataset(BaseModel):
    """Un dataset di esempio offerto da questa installazione."""

    name: str
    label: str
    description: str


class ConfigResponse(BaseModel):
    """Cosa questa installazione permette di fare. Il frontend la legge all'avvio."""

    providers: list[ProviderInfo]
    demo_mode: bool = Field(description="Se attivo, la quota è limitata e i provider fissi")
    max_questions: int = Field(description="Domande per sessione in modalità demo")
    max_upload_mb: int
    supported_extensions: list[str]
    project_questions: list[str] = Field(
        default_factory=list,
        description="Domande d'esempio sul progetto: le stesse che mostra l'app Streamlit")
    frequencies: list[str] = Field(
        default_factory=list,
        description="Frequenze accettate dal confronto tra periodi")
    demo_datasets: list[DemoDataset] = Field(
        default_factory=list,
        description="Dataset di esempio PRESENTI: l'elenco riflette il disco, non un catalogo")


# --- Dataset ------------------------------------------------------------------
class ColumnInfo(BaseModel):
    """Una colonna, come il report la vede."""

    name: str
    kind: ColumnKind
    missing_pct: float = Field(description="Percentuale di valori mancanti, 0-100")
    unique: int
    detail: str = Field(description="min/media/max se numerica, moda se testo")


class DatasetResponse(BaseModel):
    """Esito del caricamento: l'identificativo e tutto ciò che serve a impostare il report."""

    dataset_id: str = Field(description="Impronta del contenuto: stabile e riproducibile")
    label: str
    rows: int
    columns: int
    profile: list[ColumnInfo]
    measures: list[str] = Field(description="Colonne numeriche AGGREGABILI (esclusi gli ID)")
    categories: list[str] = Field(description="Colonne categoriali con cardinalità utile")
    suggested_measure: str | None
    suggested_category: str | None
    suggested_unit: str = Field(description="'$' per misure economiche senza unità indicata")
    example_questions: list[str] = Field(
        default_factory=list,
        description="Domande d'esempio costruite sulle colonne di QUESTO dataset")
    warnings: list[str] = Field(
        default_factory=list,
        description="Avvisi sul dataset appena prodotto (es. un'unione che ha "
                    "duplicato le righe). Vuoto nel caso normale.")


class FiltroSpec(BaseModel):
    """
    Restringe il dataset a certi valori di una colonna.

    Viaggia come parametro delle richieste invece di creare un "dataset filtrato"
    con un proprio identificativo: il filtro è una VISTA, non un dato nuovo, e
    materializzarlo significherebbe moltiplicare le copie in memoria a ogni
    cambio di selezione. Il costo è un `isin` per richiesta, che su un milione di
    righe è millisecondi.
    """

    column: str
    values: list[str] = Field(min_length=1)


class DistinctResponse(BaseModel):
    """Valori distinti di una colonna: servono a costruire il filtro."""

    column: str
    values: list[str]
    truncated: bool = Field(description="True se i valori distinti erano più del tetto")


# --- Confronto tra periodi ----------------------------------------------------
class PeriodRow(BaseModel):
    """Un periodo e la variazione rispetto al precedente."""

    period: str
    value: float | None
    change_pct: float | None = Field(description="None sul primo periodo: non ha un prima")


class PeriodsResponse(BaseModel):
    rows: list[PeriodRow]
    measure: str
    freq: str


# --- Unione di due dataset ----------------------------------------------------
class JoinRequest(BaseModel):
    """
    Unisce due dataset già caricati. Il risultato è un dataset NUOVO, con un
    proprio identificativo: da lì in poi il resto dell'API non sa che erano due.
    """

    left_id: str
    right_id: str
    left_on: str
    right_on: str
    how: Literal["inner", "left"] = "inner"


# --- Esportazione -------------------------------------------------------------
class ExportTurn(BaseModel):
    """Un turno come lo tiene il client: basta a ricostruire il Markdown."""

    question: str
    code: str = ""
    answer: str | None = None
    value_preview: str = Field(default="", description="Rappresentazione testuale del risultato")


class ExportRequest(BaseModel):
    turns: list[ExportTurn]
    dataset_label: str = ""


class ExportResponse(BaseModel):
    """Il Markdown pronto: il client lo salva come file."""

    markdown: str


# --- Report -------------------------------------------------------------------
class Kpi(BaseModel):
    """Una card in cima al report."""

    label: str
    value: str = Field(description="Già formattato all'italiana: il client non ricalcola")
    sub: str = ""
    tick: str = Field(description="Colore della tacca, esadecimale")


class ReportResponse(BaseModel):
    """
    Il report iniziale: numeri calcolati da Pandas, mai dal modello.

    Riporta anche le scelte APPLICATE (misura, categoria, unità): il client può
    averle lasciate vuote, e allora le decide il backend sul dataset filtrato. Un
    client che intitolasse i grafici col proprio stato scriverebbe il nome di una
    colonna diversa da quella tracciata.
    """

    measure: str | None = Field(description="La misura effettivamente usata")
    category: str | None = Field(description="La categoria effettivamente usata")
    unit: str = Field(description="L'unità effettivamente applicata")
    filter_label: str = Field(
        default="",
        description="Il filtro attivo in forma leggibile, es. \"Region ∈ {Nord, Sud}\"")
    kpis: list[Kpi]
    findings: list[str] = Field(description="Insight deterministici, non generati dall'AI")
    numeric_stats: list[dict[str, Any]] = Field(default_factory=list)
    preview: list[dict[str, Any]] = Field(description="Prime righe, per l'anteprima")
    figures: dict[str, Any] = Field(
        default_factory=dict,
        description="Figure Plotly già in JSON, per chiave: top, trend, dist, corr")


# --- Domande sui dati ---------------------------------------------------------
class AskRequest(BaseModel):
    """Una domanda in linguaggio naturale sul dataset caricato."""

    dataset_id: str
    question: str = Field(min_length=1, max_length=500)
    provider: str | None = None
    model: str | None = None
    unit: str = ""
    explain: bool = Field(
        default=True,
        description="Se falso, si restituiscono solo i numeri: niente narrazione, "
                    "e nessuna seconda chiamata al modello. Vale su entrambe le "
                    "rotte, `/ask` e `/ask/stream`")
    filtro: FiltroSpec | None = Field(
        default=None, description="Se presente, la domanda vale sul sottoinsieme filtrato")


class AskResponse(BaseModel):
    """
    L'esito di un turno. Rispecchia i tipi del backend (`ExecutionSuccess` /
    `ExecutionFailure`): `ok` distingue i due casi, `failure_kind` porta la CAUSA —
    la stessa che decide se valga la pena ritentare. Il client non deve leggere il
    messaggio per capire cosa è successo, che è l'errore che il backend ha già
    smesso di fare.
    """

    ok: bool
    question: str
    code: str = Field(description="Il codice Pandas generato, mostrabile all'utente")
    answer: str | None = Field(default=None, description="La spiegazione in italiano")
    value: Any | None = Field(default=None, description="Scalare, oppure righe di tabella")
    value_kind: ValueKind = "none"
    figure: dict[str, Any] | None = Field(default=None, description="Figura Plotly in JSON")
    columns_used: list[str] = Field(default_factory=list,
                                    description="Colonne che il codice ha davvero toccato")
    warnings: list[str] = Field(default_factory=list,
                                description="Avvisi di plausibilità ad alta confidenza")
    failure_kind: str | None = Field(
        default=None, description="syntax | security | runtime | timeout | provider | internal")
    message: str | None = Field(default=None, description="Cosa è andato storto")
    advice: str | None = Field(
        default=None, description="Cosa può farci l'utente: deriva da failure_kind")


# --- Domande sul progetto -----------------------------------------------------
class ProjectQaRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    provider: str | None = None
    model: str | None = None


class ProjectQaResponse(BaseModel):
    """Risposta fondata sulla documentazione: le fonti fanno parte della risposta."""

    answer: str
    sources: list[str] = Field(default_factory=list)


class OverviewResponse(BaseModel):
    """La sintesi in prosa del report, scritta dal modello sui numeri già calcolati."""

    text: str | None = Field(description="None se non c'è nulla da raccontare")


class ExecutiveReportResponse(BaseModel):
    """Il report esecutivo in Markdown: cinque sezioni, pronte da scaricare."""

    markdown: str


class ErrorResponse(BaseModel):
    """Forma unica degli errori: il client ne gestisce una sola."""

    detail: str
    kind: str = "error"
