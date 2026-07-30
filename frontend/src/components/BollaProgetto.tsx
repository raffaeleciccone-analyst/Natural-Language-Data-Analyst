import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { messaggioErrore } from "../api/useRichiesta";
import { Testo } from "./Testo";

/**
 * "Chiedi al progetto": il pallino in basso a destra che si apre in un riquadro.
 *
 * Risponde a domande sul LAVORO, non sui dati ("come funziona la sandbox?"), ed
 * esiste perché chi valuta questo progetto ha domande di natura diversa da chi
 * lo usa. Le risposte vengono dalla documentazione del repository e citano la
 * fonte: il modello riformula, non aggiunge fatti.
 *
 * ## Cosa cambia rispetto alla versione Streamlit
 *
 * Là la bolla era un `st.popover` reso circolare in CSS, e funzionava — ma
 * apertura e chiusura le decideva il framework, e ogni interazione ridisegnava
 * la pagina intera. Qui si può fare ciò che ci si aspetta da un widget del
 * genere e che là non era raggiungibile:
 *
 * * **Esc chiude**, come qualunque pannello sovrapposto;
 * * **il clic fuori chiude**, senza dover cercare una X;
 * * **il focus entra nel campo** all'apertura e **torna alla bolla** alla
 *   chiusura — chi naviga da tastiera non viene lasciato a metà pagina;
 * * `aria-expanded` e `role="dialog"` dicono a un lettore di schermo cosa sta
 *   succedendo.
 *
 * Nessuna di queste è una funzione in più: sono ciò che rende un pannello
 * usabile, e sono la ragione concreta per cui un frontend vero non è un lusso.
 */

interface Scambio {
  domanda: string;
  risposta: string;
  fonti: string[];
}

export function BollaProgetto({ esempi }: { esempi: string[] }) {
  const [aperta, setAperta] = useState(false);
  const [domanda, setDomanda] = useState("");
  const [storico, setStorico] = useState<Scambio[]>([]);
  const [inCorso, setInCorso] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);

  const contenitore = useRef<HTMLDivElement>(null);
  const bottoneBolla = useRef<HTMLButtonElement>(null);
  const campo = useRef<HTMLInputElement>(null);

  // Esc chiude e clic fuori chiude. I due ascoltatori vivono solo mentre il
  // pannello è aperto: registrarli sempre significherebbe far girare codice a
  // ogni clic della pagina per un pannello che di solito è chiuso.
  useEffect(() => {
    if (!aperta) return;

    const suTasto = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAperta(false);
    };
    const suClic = (e: MouseEvent) => {
      if (!contenitore.current?.contains(e.target as Node)) setAperta(false);
    };

    document.addEventListener("keydown", suTasto);
    document.addEventListener("mousedown", suClic);
    campo.current?.focus();

    // Il nodo si cattura ORA, non nel cleanup: quando il cleanup gira il ref
    // potrebbe gia' puntare altrove, e si restituirebbe il focus all'elemento
    // sbagliato (o a nulla).
    const bolla = bottoneBolla.current;

    return () => {
      document.removeEventListener("keydown", suTasto);
      document.removeEventListener("mousedown", suClic);
      // Il focus torna da dove era partito: senza, chi naviga da tastiera si
      // ritroverebbe all'inizio del documento. Sta nel CLEANUP di questo effetto
      // e non in un secondo effetto sulla stessa dipendenza: cosi' non scatta
      // anche al primo montaggio, quando ruberebbe il focus a pannello chiuso.
      bolla?.focus();
    };
  }, [aperta]);

  async function chiedi(testo: string) {
    const pulita = testo.trim();
    if (!pulita || inCorso) return;
    setInCorso(true);
    setErrore(null);
    setDomanda("");
    try {
      const r = await api.chiediAlProgetto(pulita);
      setStorico((s) => [{ domanda: pulita, risposta: r.answer, fonti: r.sources ?? [] }, ...s]);
    } catch (e) {
      setErrore(messaggioErrore(e));
    } finally {
      setInCorso(false);
    }
  }

  return (
    <div className="bolla-contenitore" ref={contenitore}>
      {aperta && (
        <div className="bolla-pannello" role="dialog" aria-label="Chiedi al progetto">
          <header>
            <strong>Chiedi al progetto</strong>
            <button
              className="collegamento"
              onClick={() => setAperta(false)}
              aria-label="Chiudi"
            >
              ✕
            </button>
          </header>
          <p className="didascalia">
            Risposte dalla documentazione del repo, con la fonte citata.
          </p>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void chiedi(domanda);
            }}
          >
            <input
              ref={campo}
              type="text"
              value={domanda}
              disabled={inCorso}
              placeholder="Es. 'Perché Pandas e non l'AI per i calcoli?'"
              onChange={(e) => setDomanda(e.target.value)}
            />
            <button type="submit" disabled={inCorso || !domanda.trim()}>
              {inCorso ? "…" : "Chiedi"}
            </button>
          </form>

          <div className="bolla-corpo">
            {errore && <div className="errore">{errore}</div>}

            {inCorso && (
              <>
                <div className="scheletro" style={{ height: 64, marginTop: 10 }} />
                <div className="scheletro" style={{ height: 14, width: "45%", marginTop: 8 }} />
              </>
            )}

            {storico.length === 0 && !inCorso && !errore && (
              <div className="esempi">
                {esempi.map((e) => (
                  <button key={e} className="esempio" onClick={() => void chiedi(e)}>
                    {e}
                  </button>
                ))}
              </div>
            )}

            {storico.map((s, i) => (
              <div className="scambio" key={storico.length - i}>
                <div className="domanda">{s.domanda}</div>
                <Testo>{s.risposta}</Testo>
                {s.fonti.length > 0 && (
                  <details>
                    <summary className="didascalia">Fonti ({s.fonti.length})</summary>
                    <ul className="fonti">
                      {s.fonti.map((f) => (
                        <li key={f}>{f}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <button
        ref={bottoneBolla}
        className="bolla"
        aria-expanded={aperta}
        aria-label="Chiedi al progetto: architettura, sicurezza, scelte tecniche"
        onClick={() => setAperta((v) => !v)}
      >
        {aperta ? "✕" : "💬"}
      </button>
    </div>
  );
}
