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

  return (
    <>
      <div className="tabella-contenitore">
        <table>
          <thead>
            <tr>
              {colonne.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {mostrate.map((riga, i) => (
              <tr key={i}>
                {colonne.map((c) => (
                  <td key={c} className={eNumerico(riga[c]) ? "numero" : undefined}>
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
