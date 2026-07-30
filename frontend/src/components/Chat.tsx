import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { AskResponse, DatasetResponse } from "../api/types";
import { Turno, TurnoInAttesa } from "./Turno";

/**
 * La conversazione sui dati.
 *
 * Lo storico e' in memoria del componente: ricaricare la pagina lo azzera, ed e'
 * una scelta e non una dimenticanza. Persisterlo significherebbe decidere DOVE
 * (localStorage espone le domande a qualunque script della pagina; il server
 * richiederebbe un'identita' che questa demo non ha) e con quale scadenza. Fino
 * a che non c'e' un motivo di prodotto, il comportamento onesto e' quello che
 * l'utente si aspetta da una pagina: si ricarica, si riparte.
 *
 * I turni piu' RECENTI stanno in alto: dopo una domanda la risposta e' dove
 * l'occhio gia' guarda, senza dover scorrere.
 */

/** Domande d'esempio costruite sulle colonne reali: dicono subito che tipo di
 *  richiesta funziona, meglio di qualunque istruzione. */
function esempi(dataset: DatasetResponse): string[] {
  const m = dataset.suggested_measure;
  const c = dataset.suggested_category;
  if (m && c) return [`Mostrami ${m} per ${c}`, `Quali sono i 5 ${c} con più ${m}?`];
  if (m) return [`Qual è il totale di ${m}?`];
  return ["Quante righe ha il dataset?"];
}

export function Chat({
  dataset,
  unita,
  provider,
}: {
  dataset: DatasetResponse;
  unita: string;
  provider?: string;
}) {
  const [domanda, setDomanda] = useState("");
  const [storico, setStorico] = useState<AskResponse[]>([]);
  const [inCorso, setInCorso] = useState<string | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  async function invia(testo: string) {
    const pulita = testo.trim();
    if (!pulita || inCorso) return;
    setInCorso(pulita);
    setDomanda("");
    setErrore(null);
    try {
      const risposta = await api.chiedi({
        dataset_id: dataset.dataset_id,
        question: pulita,
        unit: unita,
        provider: provider ?? null,
      });
      setStorico((s) => [risposta, ...s]);
    } catch (e) {
      // Un errore di CHIAMATA (rete, 404, 400) e' diverso da una risposta
      // negativa: la seconda entra nello storico come turno, questa no —
      // non c'e' stato nessun turno.
      setErrore(e instanceof ApiError ? e.message : "Errore imprevisto.");
    } finally {
      setInCorso(null);
    }
  }

  return (
    <section className="chat">
      <h2 className="sezione">Fai una domanda ai tuoi dati</h2>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void invia(domanda);
        }}
      >
        <input
          type="text"
          className="campo-domanda"
          value={domanda}
          disabled={inCorso !== null}
          placeholder="Es. 'Qual è il mese con più vendite?'"
          onChange={(e) => setDomanda(e.target.value)}
        />
        <button type="submit" disabled={inCorso !== null || !domanda.trim()}>
          {inCorso ? "Sto analizzando…" : "Invia"}
        </button>
      </form>

      {errore && <div className="errore">{errore}</div>}

      {inCorso && <TurnoInAttesa domanda={inCorso} fase="Genero il codice ed eseguo…" />}

      {storico.length === 0 && !inCorso && (
        <div className="esempi">
          <p className="didascalia">Non sai da dove iniziare? Prova una di queste.</p>
          {esempi(dataset).map((e) => (
            <button key={e} className="esempio" onClick={() => void invia(e)}>
              {e}
            </button>
          ))}
        </div>
      )}

      {storico.map((t, i) => (
        <Turno turno={t} key={storico.length - i} />
      ))}
    </section>
  );
}
