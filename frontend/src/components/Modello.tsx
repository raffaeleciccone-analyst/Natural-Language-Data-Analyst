import type { Motore } from "../api/client";
import type { ConfigResponse } from "../api/types";

/**
 * Scelta del provider LLM, del modello e della chiave.
 *
 * ## La chiave NON si salva, ed è una scelta
 *
 * Resta nello stato del componente e viaggia in un header per la singola
 * richiesta. Metterla in `localStorage` la esporrebbe a qualunque script della
 * pagina — e questa è un'applicazione che esegue codice generato da un modello.
 * Il prezzo è che ricaricando va reinserita; il prezzo dell'alternativa è molto
 * più alto.
 *
 * Il campo appare solo per i provider che una chiave la richiedono davvero:
 * chiederla per Ollama, che gira in locale, sarebbe rumore.
 *
 * ## Perché un oggetto solo e non tre stati
 *
 * Provider, modello e chiave viaggiano SEMPRE insieme — sono i tre argomenti di
 * ogni chiamata al modello. Tenerli separati significherebbe passarne tre a ogni
 * componente e ricomporli a ogni `fetch`; come `Motore` si passano una volta.
 */
export function Modello({
  config,
  motore,
  onCambia,
}: {
  config: ConfigResponse | null;
  motore: Motore;
  onCambia: (m: Motore) => void;
}) {
  if (!config?.providers.length) return null;

  const attivo = config.providers.find((p) => p.name === motore.provider) ?? config.providers[0];

  return (
    <details className="pannello-rail">
      <summary>Modello</summary>

      <span className="etichetta">Provider</span>
      <select
        value={attivo.name}
        onChange={(e) => {
          const scelto = config.providers.find((p) => p.name === e.target.value);
          // Il modello e la chiave seguono il provider: quelli di prima quasi
          // certamente non valgono sul nuovo, e la chiamata fallirebbe con un
          // errore oscuro ("model not found") invece che con un campo vuoto.
          onCambia({ provider: e.target.value, model: scelto?.default_model ?? "" });
        }}
      >
        {config.providers.map((p) => (
          <option key={p.name} value={p.name}>
            {p.name}
          </option>
        ))}
      </select>

      <span className="etichetta">Modello</span>
      <input
        type="text"
        value={motore.model ?? ""}
        placeholder={attivo.default_model}
        onChange={(e) => onCambia({ ...motore, provider: attivo.name, model: e.target.value })}
      />

      {attivo.requires_api_key && (
        <>
          <span className="etichetta">API key</span>
          <input
            type="password"
            value={motore.apiKey ?? ""}
            placeholder="lascia vuoto per usare quella del server"
            onChange={(e) => onCambia({ ...motore, provider: attivo.name, apiKey: e.target.value })}
          />
          <p className="didascalia" style={{ marginTop: 6 }}>
            Non viene salvata: vale per questa sessione.
          </p>
        </>
      )}
    </details>
  );
}
