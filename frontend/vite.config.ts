import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * In sviluppo girano DUE server: Vite (questo, col ricaricamento a caldo) e
 * uvicorn con l'API. Il proxy manda `/api` al secondo, così il codice del client
 * usa percorsi relativi e non sa dove si trovi il backend.
 *
 * È anche il motivo per cui in produzione non serve CORS: lì FastAPI serve sia
 * l'API sia questi file statici, quindi l'origine è una sola. Configurare CORS
 * per poi non usarlo è il genere di codice che resta per anni senza che nessuno
 * sappia se serve davvero.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
