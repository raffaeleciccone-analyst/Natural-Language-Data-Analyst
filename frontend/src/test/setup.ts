/**
 * Preparazione dell'ambiente di test.
 *
 * `jsdom` è un DOM finto: implementa quasi tutto, ma non ciò che dipende da un
 * motore di rendering vero. Qui si finge il minimo che questa interfaccia usa,
 * e nient'altro — ogni finzione in più è una differenza fra il test e la realtà
 * che prima o poi nasconde un difetto.
 */
import "@testing-library/jest-dom/vitest";
import { beforeEach, vi } from "vitest";

/**
 * `App` la interroga al primo render per decidere se il rail parte aperto
 * (desktop) o chiuso (telefono). Senza, jsdom solleva.
 *
 * Si ristabilisce PRIMA DI OGNI TEST, non una volta sola: un `afterEach` con
 * `vi.restoreAllMocks()` la azzererebbe, e da lì in poi ogni test fallirebbe
 * all'istante con un errore che non c'entra nulla con quello che verifica —
 * il tipo di guasto che fa perdere un'ora a cercare nel posto sbagliato.
 *
 * Il valore predefinito è "schermo largo"; chi vuole il telefono lo ridefinisce.
 */
beforeEach(() => {
Object.defineProperty(window, "matchMedia", {
  writable: true,
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});
});

// Plotly lo pretende al montaggio. I test non disegnano grafici — `Grafico` è
// caricato con `React.lazy` e in jsdom non arriva mai — ma la classe deve
// esistere perché l'import non fallisca.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
