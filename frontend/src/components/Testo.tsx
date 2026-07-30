import { Fragment, type ReactNode } from "react";

/**
 * Rende il Markdown MINIMO che i modelli producono: titoli, elenchi, grassetto e
 * codice inline.
 *
 * ## Perché non una libreria
 *
 * `react-markdown` e simili fanno molto di più — tabelle, link, immagini, HTML
 * grezzo — e ognuna di quelle cose è superficie che qui non serve. Quaranta
 * righe coprono ciò che arriva davvero, e non aggiungono un albero di dipendenze
 * a un progetto che ha appena tolto quattro vulnerabilità per non averne.
 *
 * I titoli servono al solo report esecutivo: il suo prompt chiede cinque sezioni
 * con intestazione, mentre chat e sintesi hanno il vincolo opposto («niente
 * titoli»). Senza, i `##` si leggevano come cancelletti in mezzo al testo.
 *
 * ## Perché è sicuro per costruzione
 *
 * Si costruiscono NODI REACT, mai HTML: niente `dangerouslySetInnerHTML`. Il
 * testo arriva da un modello che a sua volta ha letto documenti — è la catena
 * lungo cui un'iniezione viaggerebbe. Con i nodi React, un `<script>` nel testo
 * resta la stringa "<script>": React la scrive come testo, non la esegue. Non è
 * una difesa aggiunta, è l'impossibilità del problema.
 */

/** Spezza una riga in grassetto (**...**) e codice inline (`...`). */
function inline(riga: string, chiave: string): ReactNode[] {
  const pezzi: ReactNode[] = [];
  // Una sola espressione per entrambi: i gruppi dicono quale dei due ha combaciato.
  const regola = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  let ultimo = 0;
  let m: RegExpExecArray | null;

  while ((m = regola.exec(riga)) !== null) {
    if (m.index > ultimo) pezzi.push(riga.slice(ultimo, m.index));
    if (m[1] !== undefined) {
      pezzi.push(<strong key={`${chiave}-${m.index}`}>{m[1]}</strong>);
    } else {
      pezzi.push(<code key={`${chiave}-${m.index}`}>{m[2]}</code>);
    }
    ultimo = m.index + m[0].length;
  }
  if (ultimo < riga.length) pezzi.push(riga.slice(ultimo));
  return pezzi;
}

export function Testo({ children }: { children: string }) {
  const righe = children.split("\n");
  const blocchi: ReactNode[] = [];
  let elenco: string[] = [];

  const chiudiElenco = () => {
    if (!elenco.length) return;
    const voci = elenco;
    elenco = [];
    blocchi.push(
      <ul key={`ul-${blocchi.length}`}>
        {voci.map((v, i) => (
          <li key={i}>{inline(v, `li-${blocchi.length}-${i}`)}</li>
        ))}
      </ul>,
    );
  };

  righe.forEach((riga, i) => {
    const pulita = riga.trim();
    const voce = /^[-*•]\s+(.*)$/.exec(pulita);
    if (voce) {
      elenco.push(voce[1]);
      return;
    }
    chiudiElenco();

    const titolo = /^(#{1,4})\s+(.*)$/.exec(pulita);
    if (titolo) {
      // `#` diventa h2, non h1: l'h1 è il titolo della pagina, e un testo
      // generato dal modello non deve competere con esso — né nell'aspetto né
      // per chi naviga con uno screen reader.
      const Tag = `h${Math.min(titolo[1].length + 1, 5)}` as "h2" | "h3" | "h4" | "h5";
      blocchi.push(<Tag key={`h-${i}`}>{inline(titolo[2], `h-${i}`)}</Tag>);
      return;
    }

    if (pulita) {
      blocchi.push(<p key={`p-${i}`}>{inline(pulita, `p-${i}`)}</p>);
    }
  });
  chiudiElenco();

  return <Fragment>{blocchi}</Fragment>;
}
