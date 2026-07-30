import { useState } from "react";
import { api } from "../api/client";
import { formattaNumero } from "../api/formato";
import { useRichiesta } from "../api/useRichiesta";
import type { DatasetResponse } from "../api/types";

/**
 * Confronto di una misura tra periodi consecutivi.
 *
 * Il calcolo non è qui e non è nel modello: `nlda.periods.compare_periods` è un
 * motore deterministico, testato, che la sandbox espone anche al codice generato.
 * Questo componente sceglie i parametri e mostra la tabella.
 *
 * La variazione del primo periodo è vuota, non zero: il primo periodo non ha un
 * prima, e scrivere 0% sarebbe una bugia comoda.
 */

function Variazione({ valore }: { valore: number | null | undefined }) {
  if (valore === null || valore === undefined) return <span className="vuoto">—</span>;
  const segno = valore > 0 ? "+" : "";
  return (
    <span className={valore >= 0 ? "crescita" : "calo"}>
      {segno}
      {valore.toFixed(1).replace(".", ",")}%
    </span>
  );
}

export function Periodi({
  dataset,
  misura,
  frequenze,
}: {
  dataset: DatasetResponse;
  misura: string;
  frequenze: string[];
}) {
  const [scelta, setScelta] = useState("");
  const [freq, setFreq] = useState("trimestre");
  const [aperto, setAperto] = useState(false);

  // Le colonne data si chiedono fuori dall'apertura del pannello: servono a
  // sapere SE mostrarlo, non solo a riempirlo.
  const { dati: colonneData } = useRichiesta(
    () => api.colonneData(dataset.dataset_id),
    [dataset.dataset_id],
  );
  const disponibili = colonneData ?? [];
  // La colonna e' DERIVATA, non sincronizzata da un effetto: se la scelta non e'
  // fra quelle disponibili (altro dataset, o primo render) vale la prima.
  const colonna = disponibili.includes(scelta) ? scelta : (disponibili[0] ?? "");

  const { dati, errore } = useRichiesta(
    aperto && colonna && misura
      ? () => api.periodi(dataset.dataset_id, colonna, misura, freq)
      : null,
    [aperto, dataset.dataset_id, colonna, misura, freq],
  );

  // Senza una colonna data il confronto non ha senso: si tace invece di mostrare
  // un pannello che non può funzionare.
  if (!disponibili.length || !misura) return null;

  return (
    <details className="pannello" open={aperto} onToggle={(e) => setAperto(e.currentTarget.open)}>
      <summary>Confronto tra periodi</summary>

      <div className="controlli">
        <label>
          Colonna data
          <select value={colonna} onChange={(e) => setScelta(e.target.value)}>
            {disponibili.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          Periodo
          <select value={freq} onChange={(e) => setFreq(e.target.value)}>
            {frequenze.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </label>
      </div>

      {errore && <div className="errore">{errore}</div>}

      {dati && !errore && (
        <div className="tabella-contenitore">
          <table>
            <thead>
              <tr>
                <th>Periodo</th>
                <th>{dati.measure}</th>
                <th>Variazione</th>
              </tr>
            </thead>
            <tbody>
              {dati.rows.map((r) => (
                <tr key={r.period}>
                  <td>{r.period}</td>
                  <td className="numero">
                    {formattaNumero(r.value)}
                  </td>
                  <td className="numero">
                    <Variazione valore={r.change_pct} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {aperto && !dati && !errore && <div className="scheletro" style={{ height: 160 }} />}
    </details>
  );
}
