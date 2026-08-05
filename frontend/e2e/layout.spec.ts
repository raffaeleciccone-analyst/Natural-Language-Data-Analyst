/**
 * Il layout su telefono e tablet, MISURATO invece che guardato.
 *
 * ## Perché non bastano i test che ci sono
 *
 * I test dell'interfaccia girano su jsdom, che il DOM lo costruisce ma non lo
 * IMPAGINA: non calcola larghezze, non applica media query, non sa dire se un
 * elemento sborda. Tutto ciò che riguarda "come sta in pagina" gli è invisibile
 * per costruzione — ed è esattamente l'area che questo progetto non aveva mai
 * verificato, perché richiede un browser vero.
 *
 * ## Cosa si verifica, e perché proprio questo
 *
 * Lo scroll orizzontale è IL difetto delle pagine su telefono: si legge una
 * riga, si trascina di lato per leggerne un'altra, e l'app sembra rotta anche
 * quando i dati sono giusti. È anche il difetto più facile da introdurre — basta
 * una tabella, un titolo lungo, un grafico con una larghezza fissa — e il più
 * facile da non vedere, perché sullo schermo di chi sviluppa non succede.
 *
 * Il backend non serve: le rotte sono intercettate. Qui si misura
 * l'IMPAGINAZIONE, e legarla a un servizio che deve essere in piedi renderebbe
 * il test lento e intermittente per motivi che col layout non c'entrano.
 */
import { expect, test, type Page } from "@playwright/test";

const CONFIG = {
  providers: [{ name: "groq", default_model: "llama-3.3-70b", requires_api_key: true }],
  demo_mode: true,
  max_questions: 15,
  max_upload_mb: 25,
  max_dataset_ram_mb: 32,
  supported_extensions: ["csv", "xlsx", "json"],
  project_questions: ["Com'è fatta la sandbox?"],
  frequencies: ["mese", "trimestre", "anno"],
  demo_datasets: [
    { name: "sales", label: "Vendite (Superstore)", description: "9.800 ordini" },
  ],
};

// Un nome di colonna lungo e un valore grande: sono le due cose che allargano
// una pagina più di ogni altra, quindi il dataset finto le porta apposta.
const DATASET = {
  dataset_id: "prova",
  label: "Esempio · Vendite (Superstore)",
  rows: 9800,
  columns: 3,
  profile: [
    { name: "Region", kind: "testo", missing_pct: 0, unique: 4, detail: "più frequente: West" },
    { name: "Sales", kind: "numerica", missing_pct: 0, unique: 5000,
      detail: "min 0,44 · media 230,77 · max 22.638" },
    { name: "Nome prodotto molto lungo per mettere alla prova la colonna",
      kind: "testo", missing_pct: 0, unique: 1800, detail: "più frequente: Sedia" },
  ],
  measures: ["Sales"],
  categories: ["Region"],
  suggested_measure: "Sales",
  suggested_category: "Region",
  suggested_unit: "$",
  example_questions: ["In che anno Sales è stato più alto?"],
  warnings: [],
};

const REPORT = {
  measure: "Sales",
  category: "Region",
  unit: "$",
  filter_label: "",
  kpis: [
    { label: "Totale Sales", value: "2.261.537 $", sub: "", tick: "#15b8a1" },
    { label: "Media Sales", value: "230,77 $", sub: "", tick: "#0d8a7d" },
    { label: "Massimo Sales", value: "22.638 $", sub: "", tick: "#0d8a7d" },
    { label: "Top Region", value: "West", sub: "710.220 $", tick: "#0d8a7d" },
  ],
  findings: ["West da solo pesa il 31,40% del totale di Sales."],
  numeric_stats: [{ Colonna: "Sales", Somma: "2.261.537", Media: "230,77",
                    Minimo: "0,44", Massimo: "22.638" }],
  preview: [{ Region: "West", Sales: 100 }],
  figures: {},
};

/** Il backend, ridotto a ciò che serve per disegnare la pagina. */
async function backendFinto(page: Page) {
  await page.route("**/api/config", (r) => r.fulfill({ json: CONFIG }));
  await page.route("**/api/dataset/demo*", (r) => r.fulfill({ json: DATASET }));
  await page.route("**/api/dataset/*/report*", (r) => r.fulfill({ json: REPORT }));
  // La sintesi in prosa non arriva mai: è la condizione peggiore per il layout,
  // perché lo spazio che occuperà non è ancora noto.
  await page.route("**/api/dataset/*/overview*", () => {});
  await page.route("**/api/dataset/*/date-columns*", (r) => r.fulfill({ json: [] }));
}

/**
 * L'elenco degli elementi che sporgono oltre il bordo destro della finestra.
 *
 * Si torna il COLPEVOLE e non un booleano: "la pagina sborda di 40 px" manda a
 * cercare a mano fra centinaia di nodi, "sborda per via di `table.statistiche`"
 * dice dove mettere le mani.
 */
async function elementiCheSbordano(page: Page) {
  return page.evaluate(() => {
    const larghezza = document.documentElement.clientWidth;
    return Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .filter((el) => {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return false;
        // Chi ha uno scorrimento proprio è a posto: la tabella larga dentro il
        // suo contenitore è la soluzione, non il problema.
        const contenitore = el.closest("[data-scroll], .tabella-contenitore, pre");
        return r.right > larghezza + 1 && !contenitore;
      })
      .map((el) => `${el.tagName.toLowerCase()}.${el.className || "(senza classe)"}`)
      .slice(0, 5);
  });
}

/**
 * Aspetta che un elemento smetta di muoversi, e ne restituisce la posizione.
 *
 * La maniglia del cassetto scivola per 180 ms: leggerne il riquadro subito dopo
 * l'apertura dà il punto di PARTENZA, e un tocco lì finisce dentro il rail
 * invece che sul velo. È il difetto che ha fatto fallire questa prova due volte
 * prima che fosse la prova a sbagliare, non l'interfaccia.
 */
async function fermo(page: Page, selettore: string) {
  let precedente = "";
  for (let i = 0; i < 20; i++) {
    const r = await page.locator(selettore).boundingBox();
    const attuale = JSON.stringify(r);
    if (attuale === precedente) return r!;
    precedente = attuale;
    await page.waitForTimeout(50);
  }
  throw new Error(`${selettore} non smette di muoversi`);
}

const DISPOSITIVI = [
  { nome: "iPhone 14", larghezza: 390, altezza: 844 },
  { nome: "iPad verticale", larghezza: 820, altezza: 1180 },
];

for (const d of DISPOSITIVI) {
  test.describe(`${d.nome} (${d.larghezza}×${d.altezza})`, () => {
    test.use({ viewport: { width: d.larghezza, height: d.altezza } });

    test("la pagina non si trascina di lato", async ({ page }) => {
      await backendFinto(page);
      await page.goto("/");
      await expect(page.getByText("Totale Sales")).toBeVisible();

      const sbordano = await elementiCheSbordano(page);
      expect(sbordano, "elementi oltre il bordo destro").toEqual([]);

      const { pagina, finestra } = await page.evaluate(() => ({
        pagina: document.documentElement.scrollWidth,
        finestra: document.documentElement.clientWidth,
      }));
      expect(pagina, "la pagina è più larga della finestra").toBeLessThanOrEqual(finestra + 1);
    });

    test("i controlli si aprono e si richiudono", async ({ page }) => {
      await backendFinto(page);
      await page.goto("/");
      await expect(page.getByText("Totale Sales")).toBeVisible();

      // Sotto la soglia il rail parte CHIUSO: aprirlo su un velo scuro appena
      // arrivati vorrebbe dire nascondere la pagina prima di averla mostrata.
      const apri = page.getByRole("button", { name: "Mostra i controlli" });
      await expect(apri).toBeVisible();
      await apri.click();
      await expect(page.getByRole("button", { name: "Nascondi i controlli" })).toBeVisible();
      await expect(page.locator("#rail")).toBeVisible();

      // E si deve poter tornare indietro: un cassetto che si apre e non si
      // chiude, su un telefono, è la pagina persa.
      //
      // Si tocca la MANIGLIA, cioè il punto dove va il dito — non un elemento
      // scelto per nome. Sotto la soglia quel punto è coperto dal velo, e il
      // tocco lo prende lui: l'utente non vede la differenza, perché anche il
      // velo chiude. Un test che pretendesse il click sul pulsante fallirebbe
      // pur essendo l'interfaccia corretta, ed è esattamente quello che è
      // successo alla prima stesura di questa prova.
      const maniglia = await fermo(page, "button.interruttore-rail");
      await page.mouse.click(maniglia.x + maniglia.width / 2,
                             maniglia.y + maniglia.height / 2);
      await expect(page.locator("#rail")).toBeHidden();
    });

    test("la bolla del progetto non copre il campo della domanda", async ({ page }) => {
      await backendFinto(page);
      await page.goto("/");
      const campo = page.getByPlaceholder(/Qual è il mese/);
      await campo.scrollIntoViewIfNeeded();

      const bolla = await page.getByRole("button", { name: /progetto/i }).first().boundingBox();
      const domanda = await campo.boundingBox();
      expect(bolla && domanda).toBeTruthy();
      // Un pulsante fisso che si siede sopra il campo dove si scrive la domanda
      // renderebbe inutilizzabile proprio la cosa per cui si apre l'app.
      const sovrapposti = !(bolla!.y > domanda!.y + domanda!.height
        || domanda!.y > bolla!.y + bolla!.height
        || bolla!.x > domanda!.x + domanda!.width
        || domanda!.x > bolla!.x + bolla!.width);
      expect(sovrapposti, "la bolla copre il campo della domanda").toBe(false);
    });
  });
}
