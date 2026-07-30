import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { DatasetResponse, PeriodsResponse } from "../api/types";

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

const FREQUENZE = ["mese", "trimestre", "anno"];

function Variazione({ valore }: { valore: number | null | undefined }) {
  if (valore === null || valore === undefined) return <span className="vuoto">—</span>;
  const segno = valore > 0 ? "+" : "";
  return (
    <span className={valore >= 0 ? "crescita" : "calo"}>
      {segno}
      {valore.toLocaleString("it-IT", { maximumFractionDigits: 1 })}%
    </span>
  );
}

export function Periodi({ dataset, misura }: { dataset: DatasetResponse; misura: string }) {
  const [colonneData, setColonneData] = useState<string[]>([]);
  const [colonna, setColonna] = useState("");
  const [freq, setFreq] = useState("trimestre");
  const [dati, setDati] = useState<PeriodsResponse | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [aperto, setAperto] = useState(false);

  // Le colonne data si chiedono una volta per dataset. Fuori dall'apertura del
  // pannello: servono a sapere SE mostrarlo, non solo a riempirlo.
  useEffect(() => {
    let annullato = false;
    void api
      .colonneData(dataset.dataset_id)
      .then((c) => {
        if (annullato) return;
        setColonneData(c);
        setColonna((prec) => (c.includes(prec) ? prec : (c[0] ?? "")));
      })
      .catch(() => {
        if (!annullato) setColonneData([]);
      });
    return () => {
      annullato = true;
    };
  }, [dataset.dataset_id]);

  useEffect(() => {
    if (!aperto || !colonna || !misura) return;
    let annullato = false;
    setErrore(null);
    void api
      .periodi(dataset.dataset_id, colonna, misura, freq)
      .then((r) => {
        if (!annullato) setDati(r);
      })
      .catch((e: unknown) => {
        if (!annullato) {
          setErrore(e instanceof ApiError ? e.message : "Errore imprevisto.");
          setDati(null);
        }
      });
    return () => {
      annullato = true;
    };
  }, [aperto, dataset.dataset_id, colonna, misura, freq]);

  // Senza una colonna data il confronto non ha senso: si tace invece di mostrare
  // un pannello che non può funzionare.
  if (!colonneData.length || !misura) return null;

  return (
    <details className="pannello" open={aperto} onToggle={(e) => setAperto(e.currentTarget.open)}>
      <summary>Confronto tra periodi</summary>

      <div className="controlli">
        <label>
          Colonna data
          <select value={colonna} onChange={(e) => setColonna(e.target.value)}>
            {colonneData.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          Periodo
          <select value={freq} onChange={(e) => setFreq(e.target.value)}>
            {FREQUENZE.map((f) => (
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
                    {r.value?.toLocaleString("it-IT", { maximumFractionDigits: 2 }) ?? "—"}
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
