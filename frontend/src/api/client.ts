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
  AskRequest,
  AskResponse,
  ConfigResponse,
  DatasetResponse,
  ErrorResponse,
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

  if (!risposta.ok) {
    // L'API ha una forma d'errore sola; se per qualche motivo non arriva, non si
    // deve fallire nel gestire il fallimento.
    let dettaglio = `Errore ${risposta.status}`;
    try {
      const corpo = (await risposta.json()) as ErrorResponse;
      if (corpo?.detail) dettaglio = corpo.detail;
    } catch {
      /* risposta non JSON: resta il messaggio generico */
    }
    throw new ApiError(dettaglio, risposta.status);
  }

  return (await risposta.json()) as T;
}

function json(corpo: unknown, apiKey?: string): RequestInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  return { method: "POST", headers, body: JSON.stringify(corpo) };
}

export const api = {
  config: () => richiesta<ConfigResponse>("/config"),

  /** Carica un file. `FormData` imposta da sé il Content-Type col boundary. */
  caricaFile: (file: File) => {
    const corpo = new FormData();
    corpo.append("file", file);
    return richiesta<DatasetResponse>("/dataset", { method: "POST", body: corpo });
  },

  caricaEsempio: () => richiesta<DatasetResponse>("/dataset/demo", { method: "POST" }),

  report: (datasetId: string, opzioni: { measure?: string; category?: string; unit?: string } = {}) => {
    const q = new URLSearchParams();
    if (opzioni.measure) q.set("measure", opzioni.measure);
    if (opzioni.category) q.set("category", opzioni.category);
    if (opzioni.unit) q.set("unit", opzioni.unit);
    const coda = q.toString();
    return richiesta<ReportResponse>(
      `/dataset/${encodeURIComponent(datasetId)}/report${coda ? `?${coda}` : ""}`,
    );
  },

  chiedi: (richiestaDomanda: AskRequest, apiKey?: string) =>
    richiesta<AskResponse>("/ask", json(richiestaDomanda, apiKey)),

  chiediAlProgetto: (question: string, apiKey?: string) =>
    richiesta<ProjectQaResponse>("/project-qa", json({ question }, apiKey)),
};
