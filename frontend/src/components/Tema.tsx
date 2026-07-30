import { useEffect, useState } from "react";

/**
 * Interruttore chiaro / scuro.
 *
 * ## Perché parte dalla preferenza di sistema
 *
 * Chi usa il tema scuro l'ha già scelto una volta, nel sistema operativo:
 * chiederglielo di nuovo a ogni sito è far ripetere una decisione già presa. La
 * pagina segue `prefers-color-scheme` finché l'utente non tocca l'interruttore;
 * da quel momento vince la sua scelta esplicita, che è l'unico caso in cui è
 * giusto scavalcare il sistema.
 *
 * ## Perché `localStorage` qui va bene
 *
 * Il progetto vieta `localStorage` per la chiave API, e la regola resta: lì il
 * problema è che qualunque script della pagina potrebbe leggerla. "Chiaro o
 * scuro" non è un segreto — leggerlo non serve a nessuno, e non salvarlo
 * significherebbe rimettere la pagina in chiaro a ogni ricaricamento.
 *
 * L'attributo si scrive su `<html>` e non su un contenitore React perché i
 * colori li dichiara il CSS su `:root`: il tema deve valere anche per lo sfondo
 * della pagina oltre il contenuto, e per la barra di scorrimento.
 */
type Scelta = "chiaro" | "scuro";

const CHIAVE = "nlda-tema";

function sceltaSalvata(): Scelta | null {
  const v = localStorage.getItem(CHIAVE);
  return v === "chiaro" || v === "scuro" ? v : null;
}

function preferenzaDiSistema(): Scelta {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "scuro" : "chiaro";
}

export function Tema() {
  const [tema, setTema] = useState<Scelta>(() => sceltaSalvata() ?? preferenzaDiSistema());
  const [esplicito, setEsplicito] = useState(() => sceltaSalvata() !== null);

  useEffect(() => {
    document.documentElement.dataset.tema = tema;
  }, [tema]);

  // Finché la scelta non è esplicita, la pagina segue il sistema anche se questo
  // cambia mentre è aperta (succede con i temi automatici al tramonto).
  useEffect(() => {
    if (esplicito) return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const segui = () => setTema(preferenzaDiSistema());
    query.addEventListener("change", segui);
    return () => query.removeEventListener("change", segui);
  }, [esplicito]);

  function commuta() {
    const nuovo: Scelta = tema === "scuro" ? "chiaro" : "scuro";
    localStorage.setItem(CHIAVE, nuovo);
    setEsplicito(true);
    setTema(nuovo);
  }

  return (
    <button
      className="interruttore-tema"
      onClick={commuta}
      title={tema === "scuro" ? "Passa al tema chiaro" : "Passa al tema scuro"}
      aria-label={tema === "scuro" ? "Passa al tema chiaro" : "Passa al tema scuro"}
    >
      {tema === "scuro" ? "☀" : "☾"}
    </button>
  );
}
