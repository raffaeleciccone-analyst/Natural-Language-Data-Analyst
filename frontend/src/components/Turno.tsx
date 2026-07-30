import { useState } from "react";
import type { AskResponse } from "../api/types";
import { formattaNumero } from "../api/formato";
import { Grafico } from "./Grafico";
import { Tabella } from "./Tabella";
import { Testo } from "./Testo";

/**
 * Un turno di conversazione: domanda, risposta e tutto ciò che permette di
 * FIDARSI della risposta — le colonne che il codice ha davvero toccato, gli
 * avvisi di plausibilità, e il codice stesso.
 *
 * Il codice generato è mostrabile ma chiuso di default: chi vuole verificare lo
 * apre, chi vuole il numero non ci inciampa. Nasconderlo del tutto sarebbe la
 * scelta sbagliata in un'app che esegue codice scritto da un modello.
 */

function Valore({ turno }: { turno: AskResponse }) {
  if (turno.value === null || turno.value === undefined) return null;

  if (turno.value_kind === "table" || turno.value_kind === "series") {
    return <Tabella righe={turno.value as Record<string, unknown>[]} massimo={15} />;
  }
  if (turno.value_kind === "scalar") {
    return <div className="valore-singolo">{formattaNumero(turno.value)}</div>;
  }
  return <p>{String(turno.value)}</p>;
}

export function Turno({ turno }: { turno: AskResponse }) {
  const [codiceAperto, setCodiceAperto] = useState(false);

  return (
    <article className="turno">
      <div className="domanda">{turno.question}</div>

      {!turno.ok ? (
        <div className="errore">
          {/* Il consiglio arriva dal backend (`results.ADVICE`), accanto alla
              definizione di `FailureKind` da cui deriva anche `retryable`:
              viveva solo qui, quindi lo stesso rifiuto della sandbox dava un
              consiglio in questa interfaccia e il testo grezzo nell'altra. */}
          <strong>{turno.advice ?? "La richiesta non è andata a buon fine."}</strong>
          {turno.message && <div className="dettaglio-errore">{turno.message}</div>}
        </div>
      ) : (
        <>
          {turno.answer && (
            <div className="riquadro">
              <div className="r-etichetta">Risposta</div>
              <Testo>{turno.answer}</Testo>
            </div>
          )}

          <Valore turno={turno} />

          {turno.figure && (
            <div style={{ marginTop: 12 }}>
              <Grafico titolo="" figura={turno.figure} />
            </div>
          )}

          {turno.warnings?.map((a, i) => (
            <div className="avviso" key={i}>
              {a}
            </div>
          ))}

          {turno.columns_used?.length ? (
            <p className="didascalia">
              Colonne usate: {turno.columns_used.join(", ")}
            </p>
          ) : null}
        </>
      )}

      {turno.code && (
        <div className="codice">
          <button className="collegamento" onClick={() => setCodiceAperto((v) => !v)}>
            {codiceAperto ? "▾" : "▸"} Codice generato
          </button>
          {codiceAperto && <pre>{turno.code}</pre>}
        </div>
      )}
    </article>
  );
}

/**
 * Segnaposto del turno in arrivo.
 *
 * Non è decorativo: occupa lo spazio della risposta mentre il modello lavora —
 * che con un modello locale sono decine di secondi — così quando il testo arriva
 * non spinge in basso lo storico che l'utente sta leggendo.
 */
export function TurnoInAttesa({ domanda, fase }: { domanda: string; fase: string }) {
  return (
    <article className="turno">
      <div className="domanda">{domanda}</div>
      <p className="didascalia">{fase}</p>
      <div className="scheletro" style={{ height: 86, margin: "8px 0" }} />
      <div className="scheletro" style={{ height: 18, width: "40%" }} />
    </article>
  );
}
