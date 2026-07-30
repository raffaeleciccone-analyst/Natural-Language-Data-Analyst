import { eNumerico, formattaNumero } from "../api/formato";

/**
 * Tabella di righe generiche, come arrivano dall'API.
 *
 * Le righe sono `Record<string, unknown>` perché le colonne dipendono dal file
 * che l'utente ha caricato: non esiste un tipo più preciso da dichiarare, e
 * fingere che esista con `any` spegnerebbe il compilatore proprio dove il dato è
 * meno prevedibile. `unknown` obbliga a decidere come mostrare ogni cella, che è
 * il comportamento giusto.
 */

export function Tabella({
  righe,
  massimo = 50,
}: {
  righe: Record<string, unknown>[];
  massimo?: number;
}) {
  if (!righe.length) return <p className="vuoto">Nessuna riga da mostrare.</p>;

  const colonne = Object.keys(righe[0]);
  const mostrate = righe.slice(0, massimo);

  // Se una colonna è numerica lo decide la COLONNA, non la singola cella: i
  // numeri vanno a destra e l'intestazione deve seguirli, altrimenti "Somma"
  // resta a sinistra mentre i suoi valori stanno a destra e si legge una tabella
  // con le etichette sfalsate rispetto ai dati.
  //
  // Il verdetto si prende sulla maggioranza delle righe mostrate e non sulla
  // prima: una cella vuota o un "n/d" in cima non deve spostare l'intera colonna.
  const numerica = new Map(
    colonne.map((c) => {
      const valori = mostrate.map((r) => r[c]).filter((v) => v !== null && v !== "");
      const numerici = valori.filter(eNumerico).length;
      return [c, valori.length > 0 && numerici / valori.length > 0.5];
    }),
  );

  return (
    <>
      <div className="tabella-contenitore">
        <table>
          <thead>
            <tr>
              {colonne.map((c) => (
                <th key={c} className={numerica.get(c) ? "numero" : undefined}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {mostrate.map((riga, i) => (
              <tr key={i}>
                {colonne.map((c) => (
                  <td key={c} className={numerica.get(c) ? "numero" : undefined}>
                    {formattaNumero(riga[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {righe.length > massimo && (
        <p className="didascalia">
          Mostrate {massimo} righe su {righe.length}.
        </p>
      )}
    </>
  );
}
