import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api/client";
import type { DatasetResponse, ReportResponse } from "./api/types";
import { BollaProgetto } from "./components/BollaProgetto";
import { Chat } from "./components/Chat";
import { Grafico } from "./components/Grafico";
import { Kpi, KpiScheletro } from "./components/Kpi";
import { Tabella } from "./components/Tabella";
import "./theme.css";

/**
 * L'applicazione: il report di un dataset, con i controlli nel rail.
 *
 * Perché lo stato sta qui e non in un gestore esterno (Redux, Zustand): sono
 * cinque valori e una schermata sola. Aggiungere una libreria di stato adesso
 * significherebbe pagarne la complessità prima di avere il problema che risolve.
 * Il giorno in cui le schermate diventano tre, si sposta — è mezza giornata.
 */
export default function App() {
  const [dataset, setDataset] = useState<DatasetResponse | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [misura, setMisura] = useState("");
  const [categoria, setCategoria] = useState("");
  const [unita, setUnita] = useState("");
  const [caricamento, setCaricamento] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const inputFile = useRef<HTMLInputElement>(null);

  /** Percorso comune al caricamento di un file e del dataset di esempio. */
  const accogli = useCallback(async (promessa: Promise<DatasetResponse>) => {
    setCaricamento(true);
    setErrore(null);
    setReport(null);
    try {
      const d = await promessa;
      setDataset(d);
      setMisura(d.suggested_measure ?? "");
      setCategoria(d.suggested_category ?? "");
      setUnita(d.suggested_unit ?? "");
    } catch (e) {
      setErrore(e instanceof ApiError ? e.message : "Errore imprevisto.");
      setDataset(null);
    } finally {
      setCaricamento(false);
    }
  }, []);

  // Il report si ricarica quando cambia una delle sue dipendenze. È lo stesso
  // principio delle "signature" dell'app Streamlit — non ricalcolare l'invariato —
  // ma qui lo esprime il framework invece di una firma costruita a mano.
  useEffect(() => {
    if (!dataset) return;
    let annullato = false;
    setErrore(null);
    api
      .report(dataset.dataset_id, { measure: misura, category: categoria, unit: unita })
      .then((r) => {
        // Una risposta che arriva DOPO che l'utente ha già cambiato misura
        // sovrascriverebbe quella giusta con una vecchia: la si scarta.
        if (!annullato) setReport(r);
      })
      .catch((e: unknown) => {
        if (!annullato) setErrore(e instanceof ApiError ? e.message : "Errore imprevisto.");
      });
    return () => {
      annullato = true;
    };
  }, [dataset, misura, categoria, unita]);

  const inAttesa = caricamento || (dataset !== null && report === null && !errore);
  const fig = (nome: string) =>
    report?.figures?.[nome] as Record<string, unknown> | undefined;

  return (
    <div className="impaginazione">
      <aside className="rail">
        <h1>Natural Language Data Analyst</h1>
        <div className="sottotitolo">Interfaccia React · API FastAPI</div>

        <span className="etichetta">Dataset</span>
        <input
          ref={inputFile}
          type="file"
          accept=".csv,.xlsx,.xls,.json"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void accogli(api.caricaFile(f));
          }}
        />
        <button
          className="secondario"
          onClick={() => inputFile.current?.click()}
          disabled={caricamento}
        >
          Carica un file
        </button>
        <div style={{ height: 8 }} />
        <button
          className="secondario"
          onClick={() => void accogli(api.caricaEsempio())}
          disabled={caricamento}
        >
          Usa il dataset di esempio
        </button>

        {dataset && (
          <>
            <span className="etichetta">Misura</span>
            <select value={misura} onChange={(e) => setMisura(e.target.value)}>
              {dataset.measures.length === 0 && (
                <option value="">nessuna colonna numerica</option>
              )}
              {dataset.measures.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>

            <span className="etichetta">Categoria</span>
            <select value={categoria} onChange={(e) => setCategoria(e.target.value)}>
              {dataset.categories.length === 0 && <option value="">nessuna</option>}
              {dataset.categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>

            <span className="etichetta">Unità di misura</span>
            <input
              type="text"
              value={unita}
              placeholder="es. €, kg, %"
              onChange={(e) => setUnita(e.target.value)}
            />
          </>
        )}
      </aside>

      <main className="spazio-lavoro">
        <h1 className="titolo">Natural Language Data Analyst</h1>
        <p className="sottotitolo-pagina">
          {dataset
            ? `${dataset.label} — ${dataset.rows.toLocaleString("it-IT")} righe · ${dataset.columns} colonne`
            : "Carica un file per iniziare, oppure usa il dataset di esempio."}
        </p>

        {errore && <div className="errore">{errore}</div>}

        {!dataset && !caricamento && !errore && (
          <p className="vuoto">I numeri li calcola Pandas; il modello si limita a raccontarli.</p>
        )}

        {(dataset || caricamento) && (
          <>
            <div className="griglia-kpi">
              {report ? report.kpis.map((k, i) => <Kpi dati={k} key={i} />) : <KpiScheletro />}
            </div>

            <div className="scala" />

            <div className="colonne">
              <div>
                <h2 className="sezione">Report iniziale sui dati</h2>

            {report?.findings?.length ? (
              <div className="riquadro">
                <div className="r-etichetta">Insight automatici</div>
                <ul>
                  {report.findings.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              </div>
            ) : (
              inAttesa && (
                <div className="scheletro" style={{ height: 120, margin: "4px 0 14px" }} />
              )
            )}

            <div className="griglia-grafici">
              <Grafico
                titolo={`Classifica: ${misura || "conteggio"} per ${categoria || "categoria"}`}
                figura={fig("top")}
              />
              <Grafico titolo={`Andamento di ${misura || "conteggio"} nel tempo`} figura={fig("trend")} />
            </div>

            {(fig("dist") || inAttesa) && (
              <div style={{ marginTop: 16 }}>
                <Grafico titolo={`Distribuzione di ${misura}`} figura={fig("dist")} />
              </div>
            )}

            {report?.numeric_stats?.length ? (
              <>
                <h2 className="sezione">Statistiche delle colonne numeriche</h2>
                <Tabella righe={report.numeric_stats} />
              </>
            ) : null}

            <h2 className="sezione">Anteprima dei dati</h2>
            {report ? (
              <Tabella righe={report.preview} massimo={10} />
            ) : (
              <div className="scheletro" style={{ height: 220 }} />
            )}
              </div>

              {dataset && <Chat dataset={dataset} unita={unita} />}
            </div>
          </>
        )}
      </main>

      <BollaProgetto />
    </div>
  );
}
