/**
 * Configurazione delle prove di impaginazione.
 *
 * Un solo browser (Chromium) e un solo scopo: misurare come sta in pagina ciò
 * che jsdom non può vedere. Provare tre motori costerebbe tre volte il tempo per
 * verificare regole CSS che oggi il progetto non usa in modo esotico — se un
 * giorno servisse, si aggiunge un progetto qui.
 *
 * Il server è il BUILD, non il dev server: è ciò che finisce in produzione, e i
 * difetti di impaginazione da minificazione o da CSS ordinato diversamente si
 * vedono solo lì.
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  // In CI un `.only` dimenticato farebbe passare la suite eseguendo un test solo.
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // `--host 127.0.0.1`: senza, `vite preview` si lega al solo `localhost`
    // (che qui è IPv6) e l'attesa su 127.0.0.1 scade dopo due minuti
    // senza dire perché.
    command: "npm run build && npm run preview -- --port 4173 --strictPort --host 127.0.0.1",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
