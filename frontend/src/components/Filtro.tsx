import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { DatasetResponse, FiltroSpec } from "../api/types";

/**
 * Filtro globale su una colonna categoriale, nel rail.
 *
 * Restringe l'INTERA pagina: report, KPI, grafici e domande. Se valesse solo per
 * il report, l'utente vedrebbe i numeri di un sottoinsieme e riceverebbe risposte
 * sul totale — due verità nella stessa pagina, ed è il tipo di incoerenza che
 * fa perdere fiducia in tutto il resto.
 *
 * I valori si chiedono all'API invece di ricavarli dall'anteprima: l'anteprima è
 * di dieci righe, e un filtro costruito su quelle mostrerebbe solo i valori che
 * càpita compaiano in cima al file.
 */
export function Filtro({
  dataset,
  filtro,
  onCambia,
}: {
  dataset: DatasetResponse;
  filtro: FiltroSpec | null;
  onCambia: (f: FiltroSpec | null) => void;
}) {
  const [colonna, setColonna] = useState("");
  const [valori, setValori] = useState<string[]>([]);
  const [troncati, setTroncati] = useState(false);

  // Cambiando dataset il filtro precedente non ha più senso: si azzera invece di
  // restare attivo su una colonna che magari non esiste più.
  useEffect(() => {
    setColonna("");
    setValori([]);
    onCambia(null);
  }, [dataset.dataset_id, onCambia]);

  useEffect(() => {
    if (!colonna) {
      setValori([]);
      return;
    }
    let annullato = false;
    void api
      .distinti(dataset.dataset_id, colonna)
      .then((r) => {
        if (annullato) return;
        setValori(r.values);
        setTroncati(r.truncated);
      })
      .catch(() => {
        if (!annullato) setValori([]);
      });
    return () => {
      annullato = true;
    };
  }, [dataset.dataset_id, colonna]);

  const selezionati = filtro?.column === colonna ? filtro.values : [];

  function commuta(v: string) {
    const nuovi = selezionati.includes(v)
      ? selezionati.filter((x) => x !== v)
      : [...selezionati, v];
    onCambia(nuovi.length ? { column: colonna, values: nuovi } : null);
  }

  return (
    <>
      <span className="etichetta">Filtro</span>
      <select
        value={colonna}
        onChange={(e) => {
          setColonna(e.target.value);
          onCambia(null); // cambiando colonna, la selezione precedente non vale più
        }}
      >
        <option value="">nessun filtro</option>
        {dataset.categories.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>

      {colonna && (
        <div className="valori-filtro">
          {valori.map((v) => (
            <label key={v}>
              <input
                type="checkbox"
                checked={selezionati.includes(v)}
                onChange={() => commuta(v)}
              />
              {v}
            </label>
          ))}
          {troncati && <p className="didascalia">Mostrati i primi 500 valori.</p>}
        </div>
      )}
    </>
  );
}
