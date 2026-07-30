import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Motore } from "../api/client";
import { messaggioErrore } from "../api/useRichiesta";
import { flussoEventi } from "../api/stream";
import type { AskResponse, DatasetResponse, FiltroSpec } from "../api/types";
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

export function Chat({
  dataset,
  unita,
  filtro,
  motore,
}: {
  dataset: DatasetResponse;
  unita: string;
  filtro?: FiltroSpec | null;
  motore: Motore;
}) {
  const [domanda, setDomanda] = useState("");
  const [storico, setStorico] = useState<AskResponse[]>([]);
  const [inCorso, setInCorso] = useState<string | null>(null);
  const [fase, setFase] = useState("");
  const [errore, setErrore] = useState<string | null>(null);
  const [avvisoSpiegazione, setAvvisoSpiegazione] = useState<string | null>(null);
  const annullatore = useRef<AbortController | null>(null);

  // Smontando il componente si chiude il flusso: senza, la lettura
  // continuerebbe per una pagina che non esiste piu'.
  useEffect(() => () => annullatore.current?.abort(), []);

  /**
   * Invia la domanda e consuma il flusso di eventi.
   *
   * L'ordine in cui si aggiorna lo stato rispecchia quello degli eventi: prima
   * l'avanzamento, poi il risultato (tabella e grafico compaiono qui), infine la
   * spiegazione che si forma un pezzo alla volta. Il turno entra nello storico
   * appena arriva `result`: da quel momento e' un turno vero, e la prosa lo
   * raggiunge invece di farlo aspettare.
   */
  async function invia(testo: string) {
    const pulita = testo.trim();
    if (!pulita || inCorso) return;
    setInCorso(pulita);
    setFase("Analizzo…");
    setDomanda("");
    setErrore(null);
    setAvvisoSpiegazione(null);

    const controllo = new AbortController();
    annullatore.current = controllo;
    let indiceTurno = -1;

    try {
      for await (const evento of flussoEventi(
        "/ask/stream",
        {
          dataset_id: dataset.dataset_id,
          question: pulita,
          unit: unita,
          // La domanda vale sul sottoinsieme filtrato: altrimenti la pagina
          // mostrerebbe i numeri di una parte e risponderebbe sul totale.
          filtro: filtro ?? null,
          provider: motore.provider || null,
          model: motore.model || null,
        },
        controllo.signal,
        // La chiave nell'header, mai nel corpo che finisce nei log applicativi.
        motore.apiKey,
      )) {
        if (evento.nome === "step") {
          setFase((evento.dati as { message: string }).message);
        } else if (evento.nome === "result") {
          const turno = evento.dati as AskResponse;
          setStorico((s) => {
            indiceTurno = 0;
            return [turno, ...s];
          });
          setFase("Scrivo la spiegazione…");
        } else if (evento.nome === "token") {
          const pezzo = (evento.dati as { text: string }).text;
          // Si concatena sul turno in cima: e' quello appena inserito.
          setStorico((s) =>
            s.map((t, i) => (i === 0 ? { ...t, answer: (t.answer ?? "") + pezzo } : t)),
          );
        } else if (evento.nome === "done") {
          const finale = (evento.dati as { answer: string | null }).answer;
          // Il testo completo sostituisce i pezzi accumulati: se un evento si
          // fosse perso, qui la frase torna intera.
          if (finale !== null) {
            setStorico((s) => s.map((t, i) => (i === 0 ? { ...t, answer: finale } : t)));
          }
        } else if (evento.nome === "error") {
          const dettaglio = (evento.dati as { detail: string }).detail;
          if (indiceTurno < 0) setErrore(dettaglio);
          else setAvvisoSpiegazione(dettaglio);
        }
      }
    } catch (e) {
      setErrore(messaggioErrore(e));
    } finally {
      setInCorso(null);
      setFase("");
      annullatore.current = null;
    }
  }

  async function esporta() {
    // Il Markdown lo compone il backend (`nlda.export`), lo stesso modulo che
    // serve l'app Streamlit: comporlo qui sarebbe un secondo formato destinato a
    // divergere dal primo alla prima modifica.
    const r = await api.esporta({
      dataset_label: dataset.label,
      turns: storico
        .slice()
        .reverse()
        .map((t) => ({
          question: t.question,
          code: t.code,
          answer: t.answer ?? null,
          value_preview: t.value === null || t.value === undefined ? "" : String(t.value),
        })),
    });
    const url = URL.createObjectURL(new Blob([r.markdown], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "conversazione.md";
    a.click();
    // Senza revoca, il Blob resta in memoria finche' la pagina e' aperta.
    URL.revokeObjectURL(url);
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
          {inCorso ? "…" : "Invia"}
        </button>
      </form>

      {storico.length > 0 && (
        <div className="azioni">
          <button className="tenue" onClick={() => void esporta()}>
            ⬇ Scarica la conversazione (.md)
          </button>
        </div>
      )}

      {errore && <div className="errore">{errore}</div>}
      {avvisoSpiegazione && <div className="avviso">{avvisoSpiegazione}</div>}

      {inCorso && !storico.some((t) => t.question === inCorso) && (
        <TurnoInAttesa domanda={inCorso} fase={fase} />
      )}

      {storico.length === 0 && !inCorso && (
        <div className="esempi">
          <p className="didascalia">Non sai da dove iniziare? Prova una di queste.</p>
          {dataset.example_questions.map((e) => (
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
