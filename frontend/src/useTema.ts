import { useEffect, useState } from "react";

/**
 * Il tema attivo, per i pochi componenti che devono conoscerlo.
 *
 * Quasi tutta l'interfaccia non ne sa nulla: chiede `var(--surface)` e riceve la
 * tinta giusta. Fanno eccezione i grafici Plotly, che disegnano su un canvas e i
 * colori li vogliono come valori, non come variabili CSS.
 *
 * Si osserva l'attributo su `<html>` invece di passare il tema per props o con
 * un context: la sorgente di verità è già lì — la scrive `Tema`, la legge il CSS
 * — e duplicarla in uno stato React significherebbe poterle far divergere.
 */
export function useTema(): "chiaro" | "scuro" {
  const [tema, setTema] = useState(() => document.documentElement.dataset.tema ?? "chiaro");

  useEffect(() => {
    const osservatore = new MutationObserver(() =>
      setTema(document.documentElement.dataset.tema ?? "chiaro"),
    );
    osservatore.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-tema"],
    });
    return () => osservatore.disconnect();
  }, []);

  return tema === "scuro" ? "scuro" : "chiaro";
}
