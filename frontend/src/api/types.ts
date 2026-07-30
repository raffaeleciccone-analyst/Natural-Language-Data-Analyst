// FILE GENERATO — non modificare a mano.
// Sorgente: schema OpenAPI dell'API (nlda/api/models.py).
// Rigenera con:  python scripts/genera_tipi_ts.py
//
// Se questo file e i modelli Pydantic divergono, il compilatore TypeScript se ne
// accorge: e' il motivo per cui i tipi si generano invece di riscriverli a mano.

/** Una domanda in linguaggio naturale sul dataset caricato. */
export interface AskRequest {
  dataset_id: string;
  question: string;
  provider?: string | null;
  model?: string | null;
  unit?: string;
  explain?: boolean;
  /** Se presente, la domanda vale sul sottoinsieme filtrato */
  filtro?: FiltroSpec | null;
}

/**
 * L'esito di un turno. Rispecchia i tipi del backend (`ExecutionSuccess` /
 * `ExecutionFailure`): `ok` distingue i due casi, `failure_kind` porta la CAUSA —
 * la stessa che decide se valga la pena ritentare. Il client non deve leggere il
 * messaggio per capire cosa è successo, che è l'errore che il backend ha già
 * smesso di fare.
 */
export interface AskResponse {
  ok: boolean;
  question: string;
  /** Il codice Pandas generato, mostrabile all'utente */
  code: string;
  /** La spiegazione in italiano */
  answer?: string | null;
  /** Scalare, oppure righe di tabella */
  value?: unknown;
  value_kind?: "scalar" | "table" | "series" | "text" | "none";
  /** Figura Plotly in JSON */
  figure?: Record<string, unknown> | null;
  /** Colonne che il codice ha davvero toccato */
  columns_used?: string[];
  /** Avvisi di plausibilità ad alta confidenza */
  warnings?: string[];
  /** syntax | security | runtime | timeout | provider | internal */
  failure_kind?: string | null;
  message?: string | null;
}

/** Una colonna, come il report la vede. */
export interface ColumnInfo {
  name: string;
  kind: "numerica" | "testo" | "data" | "booleana";
  /** Percentuale di valori mancanti, 0-100 */
  missing_pct: number;
  unique: number;
  /** min/media/max se numerica, moda se testo */
  detail: string;
}

/** Cosa questa installazione permette di fare. Il frontend la legge all'avvio. */
export interface ConfigResponse {
  providers: ProviderInfo[];
  /** Se attivo, la quota è limitata e i provider fissi */
  demo_mode: boolean;
  /** Domande per sessione in modalità demo */
  max_questions: number;
  max_upload_mb: number;
  supported_extensions: string[];
}

/** Esito del caricamento: l'identificativo e tutto ciò che serve a impostare il report. */
export interface DatasetResponse {
  /** Impronta del contenuto: stabile e riproducibile */
  dataset_id: string;
  label: string;
  rows: number;
  columns: number;
  profile: ColumnInfo[];
  /** Colonne numeriche AGGREGABILI (esclusi gli ID) */
  measures: string[];
  /** Colonne categoriali con cardinalità utile */
  categories: string[];
  suggested_measure: string | null;
  suggested_category: string | null;
  /** '$' per misure economiche senza unità indicata */
  suggested_unit: string;
}

/** Valori distinti di una colonna: servono a costruire il filtro. */
export interface DistinctResponse {
  column: string;
  values: string[];
  /** True se i valori distinti erano più del tetto */
  truncated: boolean;
}

/** Forma unica degli errori: il client ne gestisce una sola. */
export interface ErrorResponse {
  detail: string;
  kind?: string;
}

export interface ExportRequest {
  turns: ExportTurn[];
  dataset_label?: string;
}

/** Il Markdown pronto: il client lo salva come file. */
export interface ExportResponse {
  markdown: string;
}

/** Un turno come lo tiene il client: basta a ricostruire il Markdown. */
export interface ExportTurn {
  question: string;
  code?: string;
  answer?: string | null;
  /** Rappresentazione testuale del risultato */
  value_preview?: string;
}

/**
 * Restringe il dataset a certi valori di una colonna.
 * Viaggia come parametro delle richieste invece di creare un "dataset filtrato"
 * con un proprio identificativo: il filtro è una VISTA, non un dato nuovo, e
 * materializzarlo significherebbe moltiplicare le copie in memoria a ogni
 * cambio di selezione. Il costo è un `isin` per richiesta, che su un milione di
 * righe è millisecondi.
 */
export interface FiltroSpec {
  column: string;
  values: string[];
}

/**
 * Unisce due dataset già caricati. Il risultato è un dataset NUOVO, con un
 * proprio identificativo: da lì in poi il resto dell'API non sa che erano due.
 */
export interface JoinRequest {
  left_id: string;
  right_id: string;
  left_on: string;
  right_on: string;
  how?: "inner" | "left";
}

/** Una card in cima al report. */
export interface Kpi {
  label: string;
  /** Già formattato all'italiana: il client non ricalcola */
  value: string;
  sub?: string;
  /** Colore della tacca, esadecimale */
  tick: string;
}

/** Un periodo e la variazione rispetto al precedente. */
export interface PeriodRow {
  period: string;
  value: number | null;
  /** None sul primo periodo: non ha un prima */
  change_pct: number | null;
}

export interface PeriodsResponse {
  rows: PeriodRow[];
  measure: string;
  freq: string;
}

export interface ProjectQaRequest {
  question: string;
  provider?: string | null;
  model?: string | null;
}

/** Risposta fondata sulla documentazione: le fonti fanno parte della risposta. */
export interface ProjectQaResponse {
  answer: string;
  sources?: string[];
}

/** Un provider LLM disponibile in questa installazione. */
export interface ProviderInfo {
  /** Identificativo da passare alle richieste, es. 'groq' */
  name: string;
  /** Modello usato se il client non ne indica uno */
  default_model: string;
  /** Se serve una chiave per usarlo */
  requires_api_key: boolean;
}

/** Il report iniziale: numeri calcolati da Pandas, mai dal modello. */
export interface ReportResponse {
  kpis: Kpi[];
  /** Insight deterministici, non generati dall'AI */
  findings: string[];
  numeric_stats?: Record<string, unknown>[];
  /** Prime righe, per l'anteprima */
  preview: Record<string, unknown>[];
  /** Figure Plotly già in JSON, per chiave: top, trend, dist, corr */
  figures?: Record<string, unknown>;
}
