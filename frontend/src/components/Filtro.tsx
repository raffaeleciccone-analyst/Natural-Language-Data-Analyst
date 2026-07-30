import { useState } from "react";
import { api } from "../api/client";
import { useRichiesta } from "../api/useRichiesta";
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
  // La colonna la COMANDA il filtro quando c'è, e lo stato locale solo quando non
  // c'è. Tenerla in uno stato indipendente lasciava il rail su "nessun filtro"
  // mentre la pagina era filtrata da un clic sulla classifica: due verità nella
  // stessa schermata, cioè proprio il difetto che questo componente esiste per
  // evitare. Lo stato locale serve al solo caso che il filtro non sa esprimere —
  // colonna scelta, nessun valore ancora spuntato.
  const [colonnaScelta, setColonnaScelta] = useState("");
  const colonna = filtro?.column ?? colonnaScelta;

  // I valori si chiedono all'API invece di ricavarli dall'anteprima: quella e' di
  // dieci righe, e un filtro costruito su quelle mostrerebbe solo i valori che
  // capita compaiano in cima al file.
  const { dati: distinti } = useRichiesta(
    colonna ? () => api.distinti(dataset.dataset_id, colonna) : null,
    [dataset.dataset_id, colonna],
  );
  const valori = distinti?.values ?? [];
  const troncati = distinti?.truncated ?? false;

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
          setColonnaScelta(e.target.value);
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
