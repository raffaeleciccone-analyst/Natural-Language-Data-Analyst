import { useState } from "react";
import { api } from "../api/client";
import type { OpzioniProsa } from "../api/client";
import { messaggioErrore } from "../api/useRichiesta";
import { Testo } from "./Testo";

/**
 * Il report esecutivo: cinque sezioni scritte dal modello sui numeri già
 * calcolati, scaricabili in Markdown.
 *
 * ## Perché SI CHIEDE invece di generarsi da solo
 *
 * A differenza della sintesi, questo non parte all'apertura della pagina: costa
 * una chiamata al modello e non a tutti serve. Generarlo d'ufficio significherebbe
 * far pagare a ogni visitatore un testo che i più non leggeranno — e nella demo
 * pubblica quella chiamata la paga il manutentore.
 *
 * ## Perché le raccomandazioni sono ipotesi
 *
 * Il prompt impone al modello di formularle come tali ("potrebbe", "suggerisce"):
 * vede numeri, non il contesto di business, e la correlazione non è causa. Un
 * imperativo sarebbe una promessa che i dati non sostengono.
 */
export function ReportEsecutivo({
  datasetId,
  opzioni,
}: {
  datasetId: string;
  opzioni: OpzioniProsa;
}) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [inCorso, setInCorso] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);

  async function genera() {
    setInCorso(true);
    setErrore(null);
    try {
      setMarkdown((await api.reportEsecutivo(datasetId, opzioni)).markdown);
    } catch (e) {
      setErrore(messaggioErrore(e));
    } finally {
      setInCorso(false);
    }
  }

  function scarica() {
    if (!markdown) return;
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "report_esecutivo.md";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <details className="pannello">
      <summary>Report esecutivo</summary>

      <p className="didascalia" style={{ marginTop: 10 }}>
        Cinque sezioni pronte da presentare, scritte sui numeri già calcolati.
      </p>

      <div className="azioni">
        <button onClick={() => void genera()} disabled={inCorso}>
          {inCorso ? "Sto scrivendo…" : markdown ? "Rigenera" : "Genera il report"}
        </button>
        {markdown && (
          <button className="tenue" onClick={scarica}>
            ⬇ Scarica (.md)
          </button>
        )}
      </div>

      {errore && <div className="errore">{errore}</div>}

      {/* Il segnaposto ha l'altezza del testo che arriverà: quando compare non
          spinge in basso quello che si sta leggendo. */}
      {inCorso && <div className="scheletro" style={{ height: 220, marginTop: 10 }} />}

      {markdown && !inCorso && (
        <div className="riquadro" style={{ marginTop: 12 }}>
          <Testo>{markdown}</Testo>
        </div>
      )}
    </details>
  );
}
