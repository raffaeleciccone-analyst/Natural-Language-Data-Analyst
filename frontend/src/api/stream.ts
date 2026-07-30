/**
 * Lettura di un flusso Server-Sent Events.
 *
 * ## Perché non `EventSource`
 *
 * `EventSource` è l'API del browser fatta apposta per SSE, e sarebbe la scelta
 * ovvia. Ma parla solo **GET**, e qui la richiesta ha un corpo: dataset, domanda,
 * filtro, unità. Infilarli nella query string significherebbe metterci la domanda
 * dell'utente — che finisce nei log del server, nella cronologia e in qualunque
 * proxy in mezzo.
 *
 * Con `fetch` si fa POST e si legge il corpo come flusso. Si perde la
 * riconnessione automatica di `EventSource`, che qui non serve: un turno che si
 * interrompe va rifatto, non ripreso da metà.
 *
 * ## Il dettaglio che rompe tutto se lo si sbaglia
 *
 * I pezzi che arrivano dalla rete NON coincidono con gli eventi: un evento può
 * essere spezzato a metà fra due pezzi, e un pezzo può contenerne tre. Si tiene
 * quindi un accumulatore e si estrae solo ciò che è terminato dal doppio a-capo.
 * Senza, si perdono eventi in modo intermittente — e sono i bug più difficili da
 * riprodurre, perché dipendono da come la rete ha suddiviso i dati.
 */
import { ApiError } from "./client";

export interface EventoSSE {
  nome: string;
  dati: unknown;
}

function analizza(blocco: string): EventoSSE | null {
  let nome = "";
  let corpo = "";
  for (const riga of blocco.split("\n")) {
    if (riga.startsWith("event: ")) nome = riga.slice(7).trim();
    else if (riga.startsWith("data: ")) corpo += riga.slice(6);
  }
  if (!nome) return null;
  try {
    return { nome, dati: corpo ? JSON.parse(corpo) : null };
  } catch {
    // Un evento malformato non deve interrompere il flusso: si salta.
    return null;
  }
}

/**
 * Esegue una POST e restituisce gli eventi man mano che arrivano.
 *
 * `AbortSignal` permette a chi chiama di chiudere il flusso — serve quando il
 * componente viene smontato: senza, la lettura continuerebbe a girare per una
 * pagina che non esiste più.
 */
export async function* flussoEventi(
  percorso: string,
  corpo: unknown,
  segnale?: AbortSignal,
): AsyncGenerator<EventoSSE> {
  let risposta: Response;
  try {
    risposta = await fetch(`/api${percorso}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
      signal: segnale,
    });
  } catch (e) {
    if ((e as Error)?.name === "AbortError") return;
    throw new ApiError("Impossibile contattare il server.", 0);
  }

  if (!risposta.ok || !risposta.body) {
    let dettaglio = `Errore ${risposta.status}`;
    try {
      const j = (await risposta.json()) as { detail?: string };
      if (j?.detail) dettaglio = j.detail;
    } catch {
      /* la risposta non era JSON: resta il messaggio generico */
    }
    throw new ApiError(dettaglio, risposta.status);
  }

  const lettore = risposta.body.pipeThrough(new TextDecoderStream()).getReader();
  let accumulato = "";

  try {
    for (;;) {
      const { done, value } = await lettore.read();
      if (done) break;
      accumulato += value;

      // Si consuma solo ciò che è completo: l'ultimo pezzo, se troncato, resta
      // nell'accumulatore e si completerà col prossimo arrivo.
      let taglio: number;
      while ((taglio = accumulato.indexOf("\n\n")) !== -1) {
        const blocco = accumulato.slice(0, taglio);
        accumulato = accumulato.slice(taglio + 2);
        const evento = analizza(blocco);
        if (evento) yield evento;
      }
    }
  } finally {
    // Rilascia la connessione anche se chi consuma smette a metà (un `break` nel
    // `for await`): senza, resterebbe aperta fino al timeout del server.
    void lettore.cancel().catch(() => undefined);
  }
}
