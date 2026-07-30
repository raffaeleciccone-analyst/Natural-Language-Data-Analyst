/**
 * La convenzione numerica italiana, come la applica il backend.
 *
 * ## Perché esiste (una divergenza vera, non ipotetica)
 *
 * `nlda/utils.py::fmt_num` formatta con separatore migliaia `.` e decimale `,`,
 * **e toglie i decimali sopra i mille**. Il client usava `toLocaleString` in
 * quattro punti, con `maximumFractionDigits` scelti a mano e diversi fra loro.
 * Risultato misurabile: `1234.56` diventa **1.235** in Streamlit e **1.234,56**
 * in React. La stessa tabella `numeric_stats`, due numeri diversi.
 *
 * Peggio ancora dentro la stessa pagina React: i KPI arrivano già formattati dal
 * backend (quindi con la regola di `fmt_num`) e le celle no — due convenzioni
 * affiancate a due centimetri di distanza.
 *
 * Questo file rispecchia `fmt_num`. Non è la soluzione ideale — quella sarebbe un
 * solo formattatore — ma il confine è HTTP e JSON non ha un tipo "numero
 * formattato". Fra due implementazioni che divergono e due che dichiarano di
 * essere la stessa cosa, con un test che lo verifica, la seconda è onesta.
 *
 * ⚠️ Se cambia `fmt_num`, cambia anche qui: `tests/test_formato.py` confronta le
 * due su una tabella di casi e diventa rosso.
 */

/** Come `nlda.utils.fmt_num`: NaN e non-numeri hanno una resa dichiarata. */
export function formattaNumero(valore: unknown): string {
  if (valore === null || valore === undefined) return "—";
  if (typeof valore === "boolean") return valore ? "vero" : "falso";

  const n = typeof valore === "number" ? valore : Number(valore);
  if (typeof valore !== "number" && (valore === "" || Number.isNaN(n))) {
    return String(valore);
  }
  if (Number.isNaN(n)) return "—";

  // La regola di fmt_num: interi e valori grandi senza decimali, i piccoli con due.
  const decimali = Number.isInteger(n) || Math.abs(n) >= 1000 ? 0 : 2;
  return n.toLocaleString("it-IT", {
    minimumFractionDigits: decimali,
    maximumFractionDigits: decimali,
  });
}

/** Vero se il valore va incolonnato a destra, in monospazio. */
export function eNumerico(valore: unknown): boolean {
  return typeof valore === "number" && !Number.isNaN(valore);
}
