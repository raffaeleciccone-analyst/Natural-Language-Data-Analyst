import { Suspense, lazy } from "react";
import { useTema } from "../useTema";

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

/** Colori degli assi in tema scuro: la griglia guida l'occhio, non compete. */
const ASSE_SCURO = { gridcolor: "#2b323b", linecolor: "#3a434e", zerolinecolor: "#3a434e" };

/**
 * Il layout della figura, adattato al tema.
 *
 * Gli assi si FONDONO invece di essere sostituiti: `xaxis` del backend porta il
 * titolo della colonna, il formato dei tick e a volte la scala logaritmica.
 * Sovrascriverlo con i soli colori avrebbe tolto il titolo agli assi — un
 * grafico piu' scuro e meno leggibile di quello che sostituiva.
 */
function conTema(layout: Record<string, unknown>, scuro: boolean): Record<string, unknown> {
  if (!scuro) return layout;
  const asse = (chiave: string) => ({
    ...((layout[chiave] as object | undefined) ?? {}),
    ...ASSE_SCURO,
  });
  return {
    ...layout,
    // Trasparente: il colore lo mette il pannello sotto, cosi' resta un valore
    // solo da tenere allineato al tema invece di due.
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { ...((layout.font as object | undefined) ?? {}), color: "#9aa6b3" },
    xaxis: asse("xaxis"),
    yaxis: asse("yaxis"),
  };
}

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
  onCategoria,
}: {
  titolo: string;
  figura?: Record<string, unknown> | null;
  /**
   * Chiamata col valore dell'elemento cliccato. La si passa solo alla
   * classifica: è l'unico grafico in cui un elemento CORRISPONDE a una
   * categoria, quindi l'unico in cui cliccare ha un significato ovvio.
   */
  onCategoria?: (valore: string) => void;
}) {
  const tema = useTema();

  return (
    <div className="pannello-grafico">
      <h3>{titolo}</h3>
      {figura ? (
        <Suspense fallback={<div className="scheletro" style={{ height: ALTEZZA }} />}>
          <PlotlyVero
            data={(figura.data as never) ?? []}
            layout={{
              // Il backend decide i colori dei DATI — sono gli stessi per le due
              // interfacce, ed e' il motivo per cui `charts.py` li conosce. La
              // SUPERFICIE su cui sono disegnati la decide chi conosce il tema,
              // cioe' il client: senza, in tema scuro restavano due rettangoli
              // bianchi in mezzo alla pagina.
              ...conTema((figura.layout as Record<string, unknown>) ?? {}, tema === "scuro"),
              autosize: true,
              // L'altezza la comanda il contenitore, non la figura: una figura che
              // porta la propria altezza sposterebbe il layout appena arriva.
              height: ALTEZZA,
              margin: { l: 55, r: 18, t: 10, b: 45 },
            }}
            config={{
              // Come in Streamlit, che mostra la barra strumenti di Plotly al
              // passaggio del mouse: zoom, pan, selezione e "scarica come PNG".
              // Toglierla aveva reso i grafici delle immagini, mentre il punto di
              // Plotly e' che si possano interrogare — e in un'analisi lo zoom su
              // un periodo e' spesso la domanda successiva.
              displayModeBar: "hover",
              displaylogo: false,
              // Il ritaglio dell'istantanea non serve qui e occupa spazio nella
              // barra; il resto (zoom, pan, reset, PNG) resta.
              modeBarButtonsToRemove: ["lasso2d", "select2d"],
              toImageButtonOptions: { format: "png", scale: 2 },
              responsive: true,
              locale: "it",
            }}
            style={{ width: "100%", height: ALTEZZA }}
            useResizeHandler
            onClick={
              onCategoria &&
              ((e) => {
                const punto = e.points?.[0] as { x?: unknown; y?: unknown } | undefined;
                // Le barre orizzontali portano la categoria su `y`, quelle
                // verticali su `x`: `charts._make_bars_readable` sceglie
                // l'orientamento in base alla lunghezza delle etichette, quindi
                // qui non si può dare per scontato quale dei due sia.
                const valore = typeof punto?.y === "string" ? punto.y : punto?.x;
                if (typeof valore === "string") onCategoria(valore);
              })
            }
          />
        </Suspense>
      ) : (
        <div className="scheletro" style={{ height: ALTEZZA }} />
      )}
    </div>
  );
}
