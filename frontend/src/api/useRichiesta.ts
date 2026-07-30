import { useEffect, useState } from "react";
import { ApiError } from "./client";

/**
 * Esegue una chiamata all'API e scarta le risposte sorpassate.
 *
 * ## Il problema che risolve
 *
 * Cambiando misura o filtro parte una richiesta nuova mentre la precedente è
 * ancora in volo. Se la vecchia arriva per ultima — succede, perché la rete non
 * garantisce l'ordine — sovrascrive il risultato giusto con uno vecchio, e la
 * pagina mostra i numeri della selezione di prima.
 *
 * Il rimedio è un flag catturato dalla chiusura, azzerato dal cleanup
 * dell'effetto. Era scritto quattro volte, con quattro copie anche della
 * traduzione dell'errore.
 *
 * ## `chiamata: null` invece di un flag separato
 *
 * I quattro siti avevano ciascuno la propria guardia (`if (!dataset) return`,
 * `if (!colonna) …`, `if (!aperto || !colonna || !misura) return`). Passare
 * `null` quando non c'è nulla da chiedere le assorbe tutte in una convenzione
 * sola, invece di aggiungere un parametro `abilitato` che ognuno userebbe a modo
 * proprio.
 */
export function useRichiesta<T>(
  chiamata: (() => Promise<T>) | null,
  deps: React.DependencyList,
): { dati: T | null; errore: string | null; inCorso: boolean } {
  const [dati, setDati] = useState<T | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const [inCorso, setInCorso] = useState(false);

  useEffect(() => {
    if (!chiamata) {
      setDati(null);
      setErrore(null);
      setInCorso(false);
      return;
    }
    let annullato = false;
    setInCorso(true);
    setErrore(null);
    // Il dato precedente si azzera: tenerlo mostrerebbe i numeri della selezione
    // di prima accanto ai controlli di quella nuova.
    setDati(null);

    chiamata()
      .then((r) => {
        if (!annullato) setDati(r);
      })
      .catch((e: unknown) => {
        if (!annullato) setErrore(messaggioErrore(e));
      })
      .finally(() => {
        if (!annullato) setInCorso(false);
      });

    return () => {
      annullato = true;
    };
    // `chiamata` è ricreata a ogni render: dipenderne farebbe ripartire la
    // richiesta all'infinito. Le dipendenze VERE le dichiara chi chiama, ed è
    // anche più leggibile — si vede da cosa dipende il dato.
    //
    // L'analizzatore non può verificare un array che arriva dall'esterno, quindi
    // avverte: è il prezzo di un hook generico, e vale il baratto — quattro copie
    // dello stesso motivo erano quattro occasioni di sbagliarlo in modo diverso.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { dati, errore, inCorso };
}

/**
 * Il messaggio da mostrare per un errore qualunque.
 *
 * Era ripetuto in cinque punti: aggiungere un secondo tipo d'errore avrebbe
 * richiesto di trovarli tutti.
 */
export function messaggioErrore(e: unknown): string {
  return e instanceof ApiError ? e.message : "Errore imprevisto.";
}
