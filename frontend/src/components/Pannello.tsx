import { useRef, type ReactNode } from "react";

/**
 * Un pannello richiudibile che, aprendosi, si porta sotto gli occhi.
 *
 * ## Il problema che risolve
 *
 * `<details>` in fondo alla pagina si apre VERSO IL BASSO: il riassunto resta
 * dov'è e il contenuto compare fuori dallo schermo. L'utente clicca, non vede
 * cambiare nulla di utile, e deve scorrere per scoprire cosa ha aperto — un
 * lavoro che gli ha dato l'interfaccia, non il compito.
 *
 * Qui, all'apertura, si porta il pannello in vista. Non si centra e non si
 * incolla in alto: si scorre **solo se serve** e solo quanto basta, perché uno
 * scatto della pagina quando non ce n'è bisogno è a sua volta disorientante.
 *
 * L'animazione è disattivata per chi ha chiesto meno movimento
 * (`prefers-reduced-motion`): uno scorrimento animato non richiesto è, per
 * qualcuno, un sintomo e non un dettaglio estetico.
 */
export function Pannello({
  titolo,
  className = "pannello",
  apertoInizialmente = false,
  children,
}: {
  titolo: ReactNode;
  className?: string;
  apertoInizialmente?: boolean;
  children: ReactNode;
}) {
  const elemento = useRef<HTMLDetailsElement>(null);

  return (
    <details
      ref={elemento}
      className={className}
      open={apertoInizialmente}
      onToggle={(e) => {
        if (!e.currentTarget.open) return;
        // Dopo il frame in cui il contenuto è stato disegnato: prima, l'altezza
        // del pannello è ancora quella chiusa e il browser scorrerebbe verso una
        // posizione che sta per cambiare.
        requestAnimationFrame(() => {
          const nodo = elemento.current;
          if (!nodo) return;
          const box = nodo.getBoundingClientRect();
          const fuoriSotto = box.bottom > window.innerHeight;
          const fuoriSopra = box.top < 0;
          if (!fuoriSotto && !fuoriSopra) return; // già tutto visibile: fermo
          nodo.scrollIntoView({
            behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
              ? "auto"
              : "smooth",
            block: box.height > window.innerHeight ? "start" : "nearest",
          });
        });
      }}
    >
      <summary>{titolo}</summary>
      {children}
    </details>
  );
}
