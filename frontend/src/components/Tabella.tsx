/**
 * Tabella di righe generiche, come arrivano dall'API.
 *
 * Le righe sono `Record<string, unknown>` perché le colonne dipendono dal file
 * che l'utente ha caricato: non esiste un tipo più preciso da dichiarare, e
 * fingere che esista con `any` spegnerebbe il compilatore proprio dove il dato è
 * meno prevedibile. `unknown` obbliga a decidere come mostrare ogni cella, che è
 * il comportamento giusto.
 */

/** Numeri a destra e in monospazio: incolonnati si confrontano a colpo d'occhio. */
function cella(valore: unknown): { testo: string; numerica: boolean } {
  if (valore === null || valore === undefined) return { testo: "—", numerica: false };
  if (typeof valore === "number") {
    // La formattazione italiana la fa il backend dove il numero ha un significato
    // (KPI, statistiche). Qui sono valori grezzi di una tabella: si usa la locale
    // del browser, che per un italiano dà la stessa convenzione.
    return { testo: valore.toLocaleString("it-IT", { maximumFractionDigits: 2 }), numerica: true };
  }
  if (typeof valore === "boolean") return { testo: valore ? "vero" : "falso", numerica: false };
  return { testo: String(valore), numerica: false };
}

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
                {colonne.map((c) => {
                  const { testo, numerica } = cella(riga[c]);
                  return (
                    <td key={c} className={numerica ? "numero" : undefined}>
                      {testo}
                    </td>
                  );
                })}
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
