/**
 * La cache delle letture, dove vive: nel client HTTP.
 *
 * Sta qui e non nel componente perché la chiave giusta è la RICHIESTA — metodo
 * e percorso — non le dipendenze di un hook, che due chiamate diverse possono
 * avere identiche (`[]` e `[]`) finendo per scambiarsi le risposte.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, svuotaCache } from "../api/client";

function rispondi(dati: unknown) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(dati) } as Response);
}

beforeEach(() => {
  svuotaCache();
  vi.restoreAllMocks();
});

describe("le letture già fatte", () => {
  it("non si richiedono due volte", async () => {
    const fetchFinto = vi.fn(() => rispondi({ measure: "Sales" }));
    vi.stubGlobal("fetch", fetchFinto);

    const primo = await api.report("abc", { measure: "Sales" });
    const secondo = await api.report("abc", { measure: "Sales" });

    expect(secondo).toEqual(primo);
    expect(fetchFinto).toHaveBeenCalledTimes(1);
  });

  it("una richiesta DIVERSA non riusa la risposta di prima", async () => {
    // È il difetto che una cache mal chiavata produce: il report filtrato che
    // mostra i numeri di quello intero, con l'aria di essere giusto.
    const fetchFinto = vi.fn((url: string) =>
      rispondi({ filter_label: url.includes("filter_values") ? "Region = West" : "" }));
    vi.stubGlobal("fetch", fetchFinto);

    const intero = await api.report("abc", {});
    const filtrato = await api.report("abc", { filtro: { column: "Region", values: ["West"] } });

    expect(intero).not.toEqual(filtrato);
    expect(fetchFinto).toHaveBeenCalledTimes(2);
  });

  it("le POST non si memorizzano", async () => {
    // Una POST può cambiare lo stato del server: riusarne la risposta
    // significherebbe raccontare un effetto che non è avvenuto.
    const fetchFinto = vi.fn(() => rispondi({ dataset_id: "x" }));
    vi.stubGlobal("fetch", fetchFinto);

    await api.caricaEsempio("sales");
    await api.caricaEsempio("sales");

    expect(fetchFinto).toHaveBeenCalledTimes(2);
  });

  it("un errore non entra in cache", async () => {
    // Altrimenti un guasto momentaneo diventerebbe permanente per tutta la
    // sessione: si ricarica la pagina e l'app è ancora rotta.
    const fetchFinto = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) } as Response)
      .mockResolvedValueOnce(await rispondi({ measure: "Sales" }));
    vi.stubGlobal("fetch", fetchFinto);

    await expect(api.report("abc", {})).rejects.toThrow();
    await expect(api.report("abc", {})).resolves.toEqual({ measure: "Sales" });
    expect(fetchFinto).toHaveBeenCalledTimes(2);
  });
});
