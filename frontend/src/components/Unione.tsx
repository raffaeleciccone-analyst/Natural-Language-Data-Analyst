import { useRef, useState } from "react";
import { api } from "../api/client";
import { messaggioErrore } from "../api/useRichiesta";
import type { DatasetResponse } from "../api/types";

/**
 * Unione con un secondo file.
 *
 * È un PREPROCESSING: il risultato è un dataset nuovo, con un proprio
 * identificativo, e da lì in poi il resto dell'applicazione non sa che erano
 * due — vede semplicemente più colonne. Report, sandbox e prompt non cambiano di
 * una riga.
 *
 * Le colonne del secondo file che si chiamano come una del primo prendono il
 * suffisso `_2`: nessuna colonna viene sovrascritta in silenzio.
 */
export function Unione({
  dataset,
  onUnito,
}: {
  dataset: DatasetResponse;
  onUnito: (nuovo: DatasetResponse) => void;
}) {
  const [secondo, setSecondo] = useState<DatasetResponse | null>(null);
  const [sinistra, setSinistra] = useState("");
  const [destra, setDestra] = useState("");
  const [tipo, setTipo] = useState<"inner" | "left">("inner");
  const [inCorso, setInCorso] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const inputFile = useRef<HTMLInputElement>(null);

  async function carica(file: File) {
    setInCorso(true);
    setErrore(null);
    try {
      const d = await api.caricaFile(file);
      setSecondo(d);
      // Se le due tabelle hanno una colonna con lo stesso nome, quasi sempre è
      // quella su cui si vogliono unire: proporla risparmia due clic.
      const comune = d.profile.map((c) => c.name)
        .find((n) => dataset.profile.some((c) => c.name === n));
      setSinistra(comune ?? "");
      setDestra(comune ?? "");
    } catch (e) {
      setErrore(messaggioErrore(e));
    } finally {
      setInCorso(false);
    }
  }

  async function unisci() {
    if (!secondo || !sinistra || !destra) return;
    setInCorso(true);
    setErrore(null);
    try {
      const unito = await api.unisci({
        left_id: dataset.dataset_id,
        right_id: secondo.dataset_id,
        left_on: sinistra,
        right_on: destra,
        how: tipo,
      });
      onUnito(unito);
      setSecondo(null);
    } catch (e) {
      setErrore(messaggioErrore(e));
    } finally {
      setInCorso(false);
    }
  }

  return (
    <details className="pannello-rail">
      <summary>Unisci un secondo dataset</summary>

      <input
        ref={inputFile}
        type="file"
        accept=".csv,.xlsx,.xls,.json"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void carica(f);
        }}
      />
      <button className="secondario" onClick={() => inputFile.current?.click()} disabled={inCorso}>
        {secondo ? `↻ ${secondo.label}` : "Scegli il secondo file"}
      </button>

      {secondo && (
        <>
          <span className="etichetta">Chiave nel primo file</span>
          <select value={sinistra} onChange={(e) => setSinistra(e.target.value)}>
            <option value="">—</option>
            {dataset.profile.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>

          <span className="etichetta">Chiave nel secondo</span>
          <select value={destra} onChange={(e) => setDestra(e.target.value)}>
            <option value="">—</option>
            {secondo.profile.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>

          <span className="etichetta">Tipo di unione</span>
          <select value={tipo} onChange={(e) => setTipo(e.target.value as "inner" | "left")}>
            <option value="inner">solo le righe con corrispondenza</option>
            <option value="left">tutte le righe del primo file</option>
          </select>

          <div style={{ height: 8 }} />
          <button onClick={() => void unisci()} disabled={inCorso || !sinistra || !destra}>
            {inCorso ? "Unisco…" : "Unisci"}
          </button>
        </>
      )}

      {errore && <div className="errore">{errore}</div>}
    </details>
  );
}
