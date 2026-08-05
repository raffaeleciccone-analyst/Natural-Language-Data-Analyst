import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/**
 * Configurazione dei test, separata da `vite.config.ts`.
 *
 * Non e' una scelta estetica: i tipi di Vitest portano con se' quelli di
 * **Rollup**, mentre Vite 8 usa **Rolldown**, e le due firme dei plugin non
 * combaciano. Tenendole nello stesso file il `tsc -b` fallisce su un conflitto
 * che non riguarda ne' il codice ne' i test.
 *
 * Questo file resta fuori dai `tsconfig` di progetto (`vite.config.ts` sta in
 * `tsconfig.node.json`, il codice in `tsconfig.app.json`), quindi il conflitto
 * non si presenta e i test girano lo stesso.
 *
 * `jsdom` e' un DOM finto in Node: serve a verificare cosa l'utente VEDE e cosa
 * succede quando clicca, non a rendere pixel.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // Solo i test che girano in jsdom. Senza questa riga Vitest raccoglieva
    // anche `e2e/*.spec.ts` — che sono prove di Playwright — e falliva con
    // "did not expect test.describe() to be called here": due strumenti che
    // usano la stessa parola per cose diverse. In locale non si vedeva, in CI
    // si': l'ordine di scoperta dei file basta a cambiare l'esito.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
