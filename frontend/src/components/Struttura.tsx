import type { DatasetResponse } from "../api/types";
import { Pannello } from "./Pannello";

/**
 * La struttura del dataset: che tipo ha ogni colonna, quanti valori mancano,
 * quanti sono distinti.
 *
 * Serve a rispondere alla domanda che viene prima di tutte le altre: "cosa c'è
 * dentro questo file?". Senza, l'utente scopre che una colonna è per metà vuota
 * solo quando un numero non torna.
 *
 * I dati arrivano già con il dataset (`profile`): nessuna chiamata in più — è
 * `loader.profile`, lo stesso che alimenta la tabella dell'app Streamlit.
 */

/** Le percentuali alte si notano: è il motivo per cui questa colonna esiste. */
function Mancanti({ percentuale }: { percentuale: number }) {
  if (percentuale === 0) return <span className="vuoto">—</span>;
  const grave = percentuale >= 20;
  return (
    <span className={grave ? "calo" : undefined}>
      {percentuale.toLocaleString("it-IT", { maximumFractionDigits: 1 })}%
    </span>
  );
}

export function Struttura({ dataset }: { dataset: DatasetResponse }) {
  return (
    <Pannello titolo={`Struttura delle colonne (${dataset.profile.length})`}>
      <div className="tabella-contenitore" style={{ marginTop: 12 }}>
        <table>
          <thead>
            <tr>
              <th>Colonna</th>
              <th>Tipo</th>
              <th>Mancanti</th>
              <th>Valori unici</th>
              <th>Dettaglio</th>
            </tr>
          </thead>
          <tbody>
            {dataset.profile.map((c) => (
              <tr key={c.name}>
                <td>
                  <strong>{c.name}</strong>
                </td>
                <td>
                  <span className="tipo-colonna">{c.kind}</span>
                </td>
                <td className="numero">
                  <Mancanti percentuale={c.missing_pct} />
                </td>
                <td className="numero">{c.unique.toLocaleString("it-IT")}</td>
                <td className="dettaglio-colonna">{c.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Pannello>
  );
}
