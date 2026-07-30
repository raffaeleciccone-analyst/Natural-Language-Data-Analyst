import { Suspense, lazy } from "react";

/**
 * Una figura Plotly che arriva dall'API già costruita e già tematizzata.
 *
 * Il frontend NON decide come si disegna un grafico: colori, orientamento delle
 * barre, scala logaritmica dell'istogramma sono decisi da `nlda/charts.py`, che
 * conosce i dati. Qui si rende soltanto — così le due interfacce mostrano
 * esattamente lo stesso grafico, che è il punto di avere un backend condiviso.
 *
 * ## Perché Plotly si carica a parte
 *
 * Plotly da solo pesa **4,8 MB** (1,4 MB compressi): importandolo normalmente
 * finiva nel bundle iniziale, e chi apre la pagina lo avrebbe scaricato prima di
 * vedere qualunque cosa — anche solo per leggere i KPI, che grafici non ne hanno.
 *
 * Con `lazy` diventa un pezzo separato, richiesto solo quando c'è davvero una
 * figura da disegnare. Il primo caricamento scende a poche decine di KB, e il
 * grafico arriva mentre l'utente sta già leggendo i numeri.
 *
 * Il segnaposto di `Suspense` ha la STESSA ALTEZZA del grafico: se fosse più
 * basso, all'arrivo della figura la pagina scatterebbe — che è esattamente il
 * difetto per cui questa interfaccia è stata scritta.
 */
const ALTEZZA = 300;

const PlotlyVero = lazy(async () => {
  const [{ default: createPlotlyComponent }, { default: Plotly }] = await Promise.all([
    import("react-plotly.js/factory"),
    import("plotly.js-dist-min"),
  ]);
  return { default: createPlotlyComponent(Plotly) };
});

export function Grafico({
  titolo,
  figura,
}: {
  titolo: string;
  figura?: Record<string, unknown> | null;
}) {
  return (
    <div className="pannello-grafico">
      <h3>{titolo}</h3>
      {figura ? (
        <Suspense fallback={<div className="scheletro" style={{ height: ALTEZZA }} />}>
          <PlotlyVero
            data={(figura.data as never) ?? []}
            layout={{
              ...((figura.layout as object) ?? {}),
              autosize: true,
              // L'altezza la comanda il contenitore, non la figura: una figura che
              // porta la propria altezza sposterebbe il layout appena arriva.
              height: ALTEZZA,
              margin: { l: 55, r: 18, t: 10, b: 45 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%", height: ALTEZZA }}
            useResizeHandler
          />
        </Suspense>
      ) : (
        <div className="scheletro" style={{ height: ALTEZZA }} />
      )}
    </div>
  );
}
