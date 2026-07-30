import { api } from "../api/client";
import type { OpzioniProsa } from "../api/client";
import { useRichiesta } from "../api/useRichiesta";
import { Testo } from "./Testo";

/**
 * La sintesi in prosa del report, scritta dal modello sui numeri già calcolati.
 *
 * Era il pezzo che il report React non aveva e quello Streamlit sì — la
 * divergenza più grande fra le due interfacce.
 *
 * ## Perché una chiamata a parte
 *
 * È l'unica parte del report che aspetta il modello: con un modello locale sono
 * decine di secondi. Se arrivasse dentro `/report` farebbe aspettare anche i
 * numeri, che sono già pronti — ed è lo stesso motivo per cui in Streamlit la
 * sintesi si genera per ultima, dopo aver disegnato tutto il resto.
 *
 * Il segnaposto ha l'altezza del testo che arriverà: quando compare non spinge in
 * basso ciò che l'utente sta già leggendo.
 */
export function Sintesi({ datasetId, opzioni }: { datasetId: string; opzioni: OpzioniProsa }) {
  const { dati, errore, inCorso } = useRichiesta(
    () => api.sintesi(datasetId, opzioni),
    // I campi uno per uno e non `opzioni`: l'oggetto e' ricostruito a ogni
    // render del genitore, quindi come dipendenza cambierebbe sempre e la
    // sintesi si rigenererebbe all'infinito.
    [datasetId, opzioni.measure, opzioni.category, opzioni.unit,
     opzioni.provider, opzioni.model, opzioni.apiKey],
  );

  // Un guasto qui non merita un riquadro rosso: i numeri ci sono tutti, manca
  // solo il commento. Resta nei log del server.
  if (errore) return null;

  if (inCorso) {
    return (
      <div className="riquadro">
        <div className="r-etichetta">Sintesi dei dati</div>
        <p className="didascalia">L'AI sta preparando la sintesi…</p>
        <div className="scheletro" style={{ height: 68 }} />
      </div>
    );
  }

  if (!dati?.text) return null;

  return (
    <div className="riquadro">
      <div className="r-etichetta">Sintesi dei dati</div>
      <Testo>{dati.text}</Testo>
    </div>
  );
}
