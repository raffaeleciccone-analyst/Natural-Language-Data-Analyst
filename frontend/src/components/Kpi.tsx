import type { Kpi as KpiDati } from "../api/types";

/**
 * Card KPI.
 *
 * Il valore arriva GIÀ FORMATTATO dall'API ("2.261.537 $"): la formattazione
 * italiana vive in `nlda/utils.py` ed è la stessa che usa l'app Streamlit.
 * Rifarla qui in JavaScript significherebbe due implementazioni della stessa
 * convenzione, destinate a divergere sul primo caso limite.
 *
 * `--vfs` è la stessa idea del tema Streamlit: quanto può essere grande il valore
 * dipende dalla larghezza della card (che il CSS conosce) E dalla lunghezza del
 * testo (che conosce solo chi lo rende). Si passa il coefficiente, e il CSS lo
 * moltiplica per la larghezza in `cqi`.
 */
export function Kpi({ dati }: { dati: KpiDati }) {
  const vfs = 153 / Math.max(dati.value.length, 1);
  return (
    <div className="kpi" style={{ "--vfs": vfs.toFixed(1) } as React.CSSProperties}>
      <div className="k-etichetta">{dati.label}</div>
      <div className="k-valore">{dati.value}</div>
      <div className="k-tacca" style={{ background: dati.tick }} />
      <div className="k-sotto">{dati.sub || " "}</div>
    </div>
  );
}

/**
 * Segnaposto delle card mentre il report arriva.
 *
 * Occupa ESATTAMENTE lo spazio delle card vere (la griglia impone
 * `grid-auto-rows: 140px`), quindi quando i numeri arrivano non spingono nulla:
 * è il punto per cui questa interfaccia esiste.
 */
export function KpiScheletro({ quanti = 4 }: { quanti?: number }) {
  return (
    <>
      {Array.from({ length: quanti }, (_, i) => (
        <div className="kpi" key={i}>
          <div className="scheletro" style={{ height: 10, width: "55%", margin: "0 auto" }} />
          <div className="scheletro" style={{ height: 26, width: "72%", margin: "14px auto 0" }} />
          <div className="scheletro" style={{ height: 5, width: 48, margin: "16px auto 0" }} />
        </div>
      ))}
    </>
  );
}
