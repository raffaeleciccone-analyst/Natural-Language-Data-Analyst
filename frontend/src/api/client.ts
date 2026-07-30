/**
 * Client HTTP dell'API, tipizzato.
 *
 * Unico punto in cui il frontend parla con il backend: le chiamate `fetch` non si
 * scrivono nei componenti, che così restano ignoranti del trasporto e testabili
 * senza rete.
 *
 * Due regole che governano il modulo:
 *
 * 1. **Un errore di rete è un'eccezione, un esito negativo no.** L'API risponde
 *    200 con `ok:false` quando la sandbox rifiuta il codice o il modello non è
 *    raggiungibile: sono esiti dell'operazione, non guasti del servizio, e chi
 *    chiama li legge dalla risposta. Qui si solleva solo per i 4xx/5xx veri.
 * 2. **La chiave API non si salva.** Viaggia in un header per la singola
 *    richiesta. Metterla in `localStorage` la esporrebbe a qualunque script della
 *    pagina, e questa è un'app che esegue codice generato da un modello.
 */
import type {
  ConfigResponse,
  DatasetResponse,
  DistinctResponse,
  ErrorResponse,
  ExecutiveReportResponse,
  ExportRequest,
  ExportResponse,
  FiltroSpec,
  JoinRequest,
  OverviewResponse,
  PeriodsResponse,
  ProjectQaResponse,
  ReportResponse,
} from "./types";

/** In sviluppo Vite fa da proxy su /api; in produzione FastAPI serve tutto. */
const BASE = "/api";

/**
 * Errore di CHIAMATA (4xx/5xx). Porta con sé lo stato, così chi mostra decide.
 *
 * Il campo è dichiarato e assegnato a mano invece di usare la proprietà nel
 * costruttore (`constructor(readonly status: number)`): quella è sintassi
 * esclusiva di TypeScript, che il progetto vieta con `erasableSyntaxOnly`. La
 * regola esiste perché così i file .ts si trasformano in .js solo CANCELLANDO i
 * tipi, senza generare codice — ed è ciò che permette agli strumenti moderni di
 * compilarli senza conoscere TypeScript.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function richiesta<T>(percorso: string, init?: RequestInit): Promise<T> {
  let risposta: Response;
  try {
    risposta = await fetch(`${BASE}${percorso}`, init);
  } catch {
    // `fetch` rifiuta solo quando la rete non ha funzionato: il server non ha
    // risposto affatto, quindi non c'è nessun dettaglio da riportare — l'errore
    // originale direbbe "Failed to fetch", che a un utente non serve. Lo stato 0
    // distingue questo caso da una risposta HTTP vera.
    throw new ApiError("Impossibile contattare il server. Controlla che sia in esecuzione.", 0);
  }

  if (!risposta.ok) throw await erroreDaRisposta(risposta);

  return (await risposta.json()) as T;
}

/**
 * Intestazioni di una richiesta JSON. Esportata perche' la usa anche `stream.ts`:
 * finche' erano due, `/ask` mandava la chiave dell'utente e `/ask/stream` no —
 * cioe' proprio la strada che la chat usa davvero.
 */
export function intestazioniJson(apiKey?: string): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  return headers;
}

/**
 * Traduce una risposta HTTP non riuscita in `ApiError`. Esportata per lo stesso
 * motivo: una forma d'errore sola merita un lettore solo.
 */
export async function erroreDaRisposta(risposta: Response): Promise<ApiError> {
  let dettaglio = `Errore ${risposta.status}`;
  try {
    const corpo = (await risposta.json()) as ErrorResponse;
    if (corpo?.detail) dettaglio = corpo.detail;
  } catch {
    /* risposta non JSON: resta il messaggio generico */
  }
  return new ApiError(dettaglio, risposta.status);
}

function json(corpo: unknown, apiKey?: string): RequestInit {
  return { method: "POST", headers: intestazioniJson(apiKey), body: JSON.stringify(corpo) };
}

/**
 * Cosa serve a una chiamata che passa dal modello: su quali colonne scrivere, e
 * con quale motore.
 *
 * `apiKey` sta qui insieme alle altre ma NON finisce nella query string: la
 * costruzione della coda la salta apposta, e va in un header. Una chiave in un
 * URL finisce nei log del server e nella cronologia del browser.
 *
 * `Motore` da solo e' quel che serve alla chat, che le colonne non le sceglie.
 */
export interface Motore {
  provider?: string;
  model?: string;
  apiKey?: string;
}

export interface OpzioniProsa extends Motore {
  measure?: string;
  category?: string;
  unit?: string;
}

/** Solo i campi valorizzati: un `measure=` vuoto direbbe al backend di scegliere
 *  una misura chiamata "", invece di lasciarlo decidere. */
function coda(opzioni: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [chiave, valore] of Object.entries(opzioni)) if (valore) q.set(chiave, valore);
  return q.toString();
}

/** Le due rotte che scrivono prosa hanno la stessa forma: stesso lettore. */
function prosa<T>(percorso: string, o: OpzioniProsa): Promise<T> {
  const q = coda({
    measure: o.measure,
    category: o.category,
    unit: o.unit,
    provider: o.provider,
    model: o.model,
  });
  return richiesta<T>(`${percorso}?${q}`, {
    method: "POST",
    headers: intestazioniJson(o.apiKey),
  });
}

export const api = {
  /** Cosa permette questa installazione: provider, limiti, suggerimenti. */
  config: () => richiesta<ConfigResponse>("/config"),

  /** Carica un file. `FormData` imposta da sé il Content-Type col boundary. */
  caricaFile: (file: File) => {
    const corpo = new FormData();
    corpo.append("file", file);
    return richiesta<DatasetResponse>("/dataset", { method: "POST", body: corpo });
  },

  caricaEsempio: () => richiesta<DatasetResponse>("/dataset/demo", { method: "POST" }),

  report: (
    datasetId: string,
    opzioni: {
      measure?: string;
      category?: string;
      unit?: string;
      filtro?: FiltroSpec | null;
    } = {},
  ) => {
    const q = new URLSearchParams(
      coda({
        measure: opzioni.measure,
        category: opzioni.category,
        unit: opzioni.unit,
        filter_column: opzioni.filtro?.column,
      }),
    );
    // Ripetuto una volta per valore: e' il modo in cui FastAPI legge una lista.
    if (opzioni.filtro) opzioni.filtro.values.forEach((v) => q.append("filter_values", v));
    const stringa = q.toString();
    return richiesta<ReportResponse>(
      `/dataset/${encodeURIComponent(datasetId)}/report${stringa ? `?${stringa}` : ""}`,
    );
  },

  /** Valori distinti di una colonna: servono a costruire il filtro. */
  distinti: (datasetId: string, column: string) =>
    richiesta<DistinctResponse>(
      `/dataset/${encodeURIComponent(datasetId)}/distinct?column=${encodeURIComponent(column)}`,
    ),

  colonneData: (datasetId: string) =>
    richiesta<string[]>(`/dataset/${encodeURIComponent(datasetId)}/date-columns`),

  periodi: (datasetId: string, dateColumn: string, measure: string, freq: string) => {
    const q = new URLSearchParams({ date_column: dateColumn, measure, freq });
    return richiesta<PeriodsResponse>(
      `/dataset/${encodeURIComponent(datasetId)}/periods?${q.toString()}`,
    );
  },

  esporta: (corpo: ExportRequest) => richiesta<ExportResponse>("/export", json(corpo)),

  /**
   * La sintesi in prosa del report. Rotta a parte, come in Streamlit: e' l'unica
   * cosa che aspetta il modello, e nel report farebbe aspettare anche i numeri.
   */
  sintesi: (datasetId: string, opzioni: OpzioniProsa) =>
    prosa<OverviewResponse>(`/dataset/${encodeURIComponent(datasetId)}/overview`, opzioni),

  /**
   * Il report esecutivo in Markdown. A differenza della sintesi si CHIEDE: costa
   * una chiamata al modello e non a tutti serve.
   */
  reportEsecutivo: (datasetId: string, opzioni: OpzioniProsa) =>
    prosa<ExecutiveReportResponse>(
      `/dataset/${encodeURIComponent(datasetId)}/executive-report`,
      opzioni,
    ),

  /** Unisce due dataset gia' caricati: il risultato e' un dataset NUOVO. */
  unisci: (corpo: JoinRequest) => richiesta<DatasetResponse>("/dataset/join", json(corpo)),

  chiediAlProgetto: (question: string, apiKey?: string) =>
    richiesta<ProjectQaResponse>("/project-qa", json({ question }, apiKey)),
};
