/**
 * Test dell'applicazione, dal punto di vista di chi la usa.
 *
 * ## Cosa verificano e cosa no
 *
 * Non verificano che i numeri siano giusti: quelli li calcola Pandas e li
 * coprono i test Python. Qui si verifica il COMPORTAMENTO dell'interfaccia —
 * cosa compare, cosa succede quando si clicca, cosa si azzera quando si cambia
 * dataset. È la parte che in questo progetto si è rotta di più.
 *
 * ## Perché proprio questi casi
 *
 * Ognuno corrisponde a un difetto REALE arrivato fino all'utente:
 *
 * * la pagina si apriva vuota e chiedeva di scegliere un file prima di aver
 *   mostrato niente;
 * * cambiando dataset partiva una sintesi con le colonne del precedente —
 *   una chiamata al modello sprecata su colonne inesistenti;
 * * chiudendo la barra laterale il contenuto si riduceva a 88 px;
 * * cliccando una barra della classifica il filtro non compariva nel rail, che
 *   continuava a dire "nessun filtro" mentre la pagina era filtrata.
 *
 * Un test scritto dopo il difetto vale più di dieci scritti a caso: descrive
 * qualcosa che è successo davvero, non qualcosa che potrebbe succedere.
 *
 * La rete è finta (`vi.mock` su `../api/client`): questi test non devono
 * dipendere da un server acceso, altrimenti falliscono per motivi che non
 * riguardano l'interfaccia.
 */
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { ApiError } from "../api/client";
import type { ConfigResponse, DatasetResponse, ReportResponse } from "../api/types";

// --- I dati finti --------------------------------------------------------------
// Due dataset con colonne DIVERSE: è ciò che rende osservabile il difetto del
// cambio dataset. Con due dataset uguali quel test non proverebbe nulla.
const VENDITE: DatasetResponse = {
  dataset_id: "id-vendite",
  label: "Esempio · Vendite (Superstore)",
  rows: 9800,
  columns: 3,
  profile: [],
  measures: ["Sales"],
  categories: ["Region", "Segment"],
  suggested_measure: "Sales",
  suggested_category: "Region",
  suggested_unit: "$",
  example_questions: ["In che anno Sales è stato più alto?"],
};

const FILM: DatasetResponse = {
  ...VENDITE,
  dataset_id: "id-film",
  label: "Esempio · Film (box office)",
  rows: 1830,
  measures: ["US Gross"],
  categories: ["Creative Type"],
  suggested_measure: "US Gross",
  suggested_category: "Creative Type",
};

const CONFIG: ConfigResponse = {
  providers: [{ name: "groq", default_model: "llama", requires_api_key: true }],
  demo_mode: false,
  max_questions: 0,
  max_upload_mb: 25,
  max_dataset_ram_mb: 256,
  supported_extensions: ["csv"],
  project_questions: [],
  frequencies: ["mese"],
  demo_datasets: [
    { name: "sales", label: "Vendite (Superstore)", description: "9.800 ordini" },
    { name: "films", label: "Film (box office)", description: "1.830 film" },
  ],
};

function reportDi(d: DatasetResponse, filtro?: string): ReportResponse {
  return {
    measure: d.suggested_measure,
    category: d.suggested_category,
    unit: "$",
    filter_label: filtro ?? "",
    kpis: [{ label: `Totale ${d.suggested_measure}`, value: "1.000 $", sub: "", tick: "#0d8a7d" }],
    findings: [],
    numeric_stats: [],
    preview: [{ [d.suggested_category!]: "Nord", [d.suggested_measure!]: 100 }],
    figures: {},
  };
}

// --- Il client finto ------------------------------------------------------------
// `vi.hoisted`: `vi.mock` viene issato in cima al file, prima di qualunque
// dichiarazione, quindi una `const` normale qui sarebbe ancora inizializzata
// quando la fabbrica del mock la usa. `hoisted` sale insieme a lui.
const api = vi.hoisted(() => ({
  config: vi.fn(),
  caricaEsempio: vi.fn(),
  caricaFile: vi.fn(),
  report: vi.fn(),
  sintesi: vi.fn(),
  reportEsecutivo: vi.fn(),
  distinti: vi.fn(),
  colonneData: vi.fn(),
  periodi: vi.fn(),
  esporta: vi.fn(),
  unisci: vi.fn(),
  chiediAlProgetto: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const vero = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...vero, api };
});

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.config.mockResolvedValue(CONFIG);
  api.caricaEsempio.mockImplementation(async (nome?: string) =>
    nome === "films" ? FILM : VENDITE);
  api.report.mockImplementation(async (id: string, o: { filtro?: { values: string[] } | null }) =>
    reportDi(id === "id-film" ? FILM : VENDITE,
             o?.filtro ? `Region = ${o.filtro.values[0]}` : ""));
  // La sintesi non arriva mai: l'interfaccia deve restare usabile lo stesso.
  api.sintesi.mockImplementation(() => new Promise(() => {}));
  api.distinti.mockResolvedValue({ column: "Region", values: ["Nord", "Sud"], truncated: false });
  api.colonneData.mockResolvedValue([]);
});

// Niente `vi.restoreAllMocks()`: azzererebbe anche la finzione di `matchMedia`
// installata in `setup.ts`. I mock del client li ripulisce gia' `beforeEach`.
afterEach(() => cleanup());

/**
 * Attende che un dataset sia caricato. Si aspetta l'ETICHETTA, non il numero di
 * righe: il Node dei test ha `small-icu` (solo dati inglesi), quindi
 * `toLocaleString("it-IT")` qui rende `9800` dove il browser rende `9.800`. Un
 * test che verificasse la formattazione fallirebbe per l'ambiente, non per un
 * difetto — e passerebbe di nuovo su un'altra macchina, che è il peggio.
 */
async function attendi(etichetta: RegExp) {
  await waitFor(() => expect(screen.getByText(etichetta)).toBeInTheDocument());
}

/**
 * Il selettore della colonna da filtrare.
 *
 * Non "l'ultimo `combobox`": dopo di lui, nel gruppo Avanzate, c'e' quello del
 * provider. Si riconosce dall'opzione vuota "nessun filtro", che e' sua e di
 * nessun altro — un indizio che sopravvive a un riordino del rail.
 */
function selettoreDelFiltro(): HTMLSelectElement {
  const trovato = screen.getAllByRole("combobox").find((s) =>
    within(s).queryByRole("option", { name: "nessun filtro" }));
  if (!trovato) throw new Error("selettore del filtro non trovato");
  return trovato as HTMLSelectElement;
}

async function conDatasetPronto() {
  render(<App />);
  // Si aspetta il KPI, non l'etichetta del dataset: fra i due c'e' la chiamata
  // al report, e fermarsi prima significa fare asserzioni su una pagina che sta
  // ancora mostrando gli scheletri.
  await attendi(/Totale Sales/);
}

// --- I test ---------------------------------------------------------------------
describe("apertura", () => {
  it("carica da sola il primo dataset di esempio", async () => {
    await conDatasetPronto();

    // Chi arriva non sa cosa fa l'app: chiedergli di scegliere un file prima di
    // avergli mostrato qualcosa e' chiedere una decisione senza gli elementi.
    expect(api.caricaEsempio).toHaveBeenCalledWith("sales");
    expect(screen.getByText(/Totale Sales/)).toBeInTheDocument();
    expect(screen.queryByText(/Carica un file per iniziare/)).not.toBeInTheDocument();
  });

  it("non ricarica l'esempio a ogni render", async () => {
    await conDatasetPronto();
    await new Promise((r) => setTimeout(r, 50));
    // `StrictMode` invoca gli effetti due volte in sviluppo: la guardia
    // `avviato` esiste per questo.
    expect(api.caricaEsempio).toHaveBeenCalledTimes(1);
  });

  it("l'anteprima parte chiusa", async () => {
    await conDatasetPronto();
    const pannello = screen.getByText("Anteprima dei dati").closest("details");
    expect(pannello).not.toHaveAttribute("open");
  });
});

describe("cambio di dataset", () => {
  it("azzera le scelte sulle colonne del nuovo file", async () => {
    const utente = userEvent.setup();
    await conDatasetPronto();
    expect(screen.getByDisplayValue("Sales")).toBeInTheDocument();

    await utente.click(screen.getByRole("button", { name: /Film \(box office\)/ }));

    await attendi(/Totale US Gross/);
    // Le scelte seguono il file nuovo, non restano quelle di prima.
    expect(screen.getByDisplayValue("US Gross")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Sales")).not.toBeInTheDocument();
  });

  it("non chiede la sintesi con le colonne del dataset precedente", async () => {
    const utente = userEvent.setup();
    await conDatasetPronto();
    api.sintesi.mockClear();

    await utente.click(screen.getByRole("button", { name: /Film \(box office\)/ }));
    await attendi(/Totale US Gross/);

    // Il difetto: per un istante l'id era gia' del file nuovo e le colonne
    // ancora del vecchio, e la sintesi partiva davvero — una chiamata al
    // modello su colonne che quel dataset non ha.
    for (const chiamata of api.sintesi.mock.calls) {
      const [id, opzioni] = chiamata as [string, { measure?: string }];
      if (id === "id-film") {
        expect(opzioni.measure).not.toBe("Sales");
      }
    }
  });
});

describe("filtro", () => {
  it("il rail mostra il filtro impostato da fuori", async () => {
    const utente = userEvent.setup();
    await conDatasetPronto();

    // Si sceglie la colonna nel rail: il filtro compare in cima alla pagina.
    await utente.selectOptions(selettoreDelFiltro(), "Region");
    await waitFor(() => expect(screen.getByLabelText("Nord")).toBeInTheDocument());
    await utente.click(screen.getByLabelText("Nord"));

    await waitFor(() => expect(screen.getByText(/Filtro attivo/)).toBeInTheDocument());
    // La casella resta spuntata: il rail e la pagina dicono la stessa cosa.
    expect(screen.getByLabelText("Nord")).toBeChecked();
  });

  it("si toglie dal riquadro in cima", async () => {
    const utente = userEvent.setup();
    await conDatasetPronto();
    await utente.selectOptions(selettoreDelFiltro(), "Region");
    await waitFor(() => expect(screen.getByLabelText("Sud")).toBeInTheDocument());
    await utente.click(screen.getByLabelText("Sud"));
    await waitFor(() => expect(screen.getByText(/Filtro attivo/)).toBeInTheDocument());

    await utente.click(screen.getByRole("button", { name: /Togli il filtro/ }));
    await waitFor(() => expect(screen.queryByText(/Filtro attivo/)).not.toBeInTheDocument());
  });
});

describe("barra laterale", () => {
  it("si chiude e si riapre senza portarsi via il contenuto", async () => {
    const utente = userEvent.setup();
    await conDatasetPronto();
    const rail = document.querySelector(".rail")!;
    expect(rail).not.toHaveAttribute("hidden");

    await utente.click(screen.getByRole("button", { name: /Nascondi i controlli/ }));
    expect(document.querySelector(".rail")).toHaveAttribute("hidden");
    // Il difetto era qui: il <main> scivolava in una colonna larga zero.
    expect(document.querySelector(".impaginazione")).toHaveClass("rail-chiuso");
    expect(screen.getByText(/Totale Sales/)).toBeVisible();

    await utente.click(screen.getByRole("button", { name: /Mostra i controlli/ }));
    expect(document.querySelector(".rail")).not.toHaveAttribute("hidden");
  });

  it("sul telefono parte chiusa", async () => {
    // Aprire l'app su un velo scuro coi controlli davanti al report e' il modo
    // migliore per non far capire cosa fa.
    vi.mocked(window.matchMedia).mockImplementation((query: string) =>
      ({ matches: true, media: query, onchange: null,
         addEventListener: vi.fn(), removeEventListener: vi.fn(),
         addListener: vi.fn(), removeListener: vi.fn(),
         dispatchEvent: vi.fn() }) as unknown as MediaQueryList);

    render(<App />);
    await attendi(/Totale Sales/);
    expect(document.querySelector(".rail")).toHaveAttribute("hidden");
  });
});

describe("quando qualcosa va storto", () => {
  it("un caricamento fallito lo dice, e non lascia la pagina a metà", async () => {
    // `ApiError` e non `Error`: `messaggioErrore` mostra solo i messaggi degli
    // errori dell'API, e per tutto il resto dice "Errore imprevisto". E' voluto
    // — il testo di un'eccezione interna non e' roba da mettere in pagina — e il
    // test deve simulare il caso vero, non quello comodo.
    api.caricaEsempio.mockRejectedValue(new ApiError("server irraggiungibile", 503));
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/server irraggiungibile/)).toBeInTheDocument());
    expect(screen.getByText(/Carica un file per iniziare/)).toBeInTheDocument();
  });

  it("la sintesi che non arriva non blocca il report", async () => {
    // La narrativa e' un complemento: i numeri di Pandas si vedono comunque.
    await conDatasetPronto();
    expect(screen.getByText(/Totale Sales/)).toBeInTheDocument();
    expect(within(document.querySelector(".griglia-kpi")!).getByText("1.000 $"))
      .toBeInTheDocument();
  });
});

describe("modalità demo", () => {
  it("dice il limite invece di offrire i modelli", async () => {
    api.config.mockResolvedValue({ ...CONFIG, demo_mode: true, max_questions: 15 });
    await conDatasetPronto();

    // Un menu che promette cinque provider mentre uno solo ha una chiave
    // configurata e' un'interfaccia che mente.
    expect(screen.getByText(/Demo pubblica/)).toBeInTheDocument();
    expect(screen.getByText(/15 domande a testa/)).toBeInTheDocument();
    expect(screen.queryByText("Provider")).not.toBeInTheDocument();
  });
});
