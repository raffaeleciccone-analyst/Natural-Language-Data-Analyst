import { useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import type { Motore, OpzioniProsa } from "./api/client";
import { messaggioErrore, useRichiesta } from "./api/useRichiesta";
import { Tema } from "./components/Tema";
import { Sintesi } from "./components/Sintesi";
import type { DatasetResponse, FiltroSpec } from "./api/types";
import { BollaProgetto } from "./components/BollaProgetto";
import { Chat } from "./components/Chat";
import { Filtro } from "./components/Filtro";
import { Grafico } from "./components/Grafico";
import { Kpi, KpiScheletro } from "./components/Kpi";
import { Modello } from "./components/Modello";
import { Pannello } from "./components/Pannello";
import { Periodi } from "./components/Periodi";
import { ReportEsecutivo } from "./components/ReportEsecutivo";
import { Struttura } from "./components/Struttura";
import { Tabella } from "./components/Tabella";
import { Unione } from "./components/Unione";
import "./theme.css";

/** Larghezza sotto la quale il rail diventa un cassetto. Vedi `theme.css`. */
const SOGLIA_STRETTA = 820;

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
  const [misura, setMisura] = useState("");
  const [categoria, setCategoria] = useState("");
  const [unita, setUnita] = useState("");
  const [caricamento, setCaricamento] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<FiltroSpec | null>(null);
  const [motore, setMotore] = useState<Motore>({});
  // Aperto sul desktop, CHIUSO sul telefono: li' e' un cassetto che copre la
  // pagina, e aprire l'app su un velo scuro con i controlli davanti al contenuto
  // e' il modo migliore per non far capire cosa fa. La soglia e' la stessa del
  // CSS; sta in un posto solo perche' due valori che devono coincidere e non
  // possono verificarsi a vicenda prima o poi divergono.
  const [railAperto, setRailAperto] = useState(
    () => !window.matchMedia(`(max-width: ${SOGLIA_STRETTA}px)`).matches,
  );
  const inputFile = useRef<HTMLInputElement>(null);

  // La configurazione si chiede una volta all'avvio: contiene i suggerimenti e le
  // frequenze, cioe' liste che il backend possiede e che il client ribatteva.
  const { dati: config } = useRichiesta(() => api.config(), []);

  // All'apertura si carica da solo il primo dataset di esempio.
  //
  // Prima la pagina si apriva vuota, con una frase e tre pulsanti: chi arriva
  // non sa ancora cosa fa l'app, e chiedergli di scegliere un file PRIMA di
  // avergli mostrato qualcosa e' chiedere una decisione senza gli elementi per
  // prenderla. Ora vede subito un report vero e i pulsanti restano li' per
  // cambiare dataset o caricare il proprio.
  //
  // `dataset === null` nella condizione, non un flag "primo avvio": se l'utente
  // ha gia' caricato qualcosa questo non deve sovrascriverglielo.
  const [avviato, setAvviato] = useState(false);
  useEffect(() => {
    if (avviato || dataset || caricamento || !config?.demo_datasets?.length) return;
    setAvviato(true);
    void accogli(api.caricaEsempio(config.demo_datasets[0].name));
    // `accogli` e `dataset` cambiano a ogni render: la guardia sopra e' cio' che
    // impedisce il ciclo, non l'elenco delle dipendenze.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, avviato]);

  /** Percorso comune al caricamento di un file e del dataset di esempio. */
  async function accogli(promessa: Promise<DatasetResponse>) {
    setCaricamento(true);
    setErrore(null);
    try {
      const d = await promessa;
      setDataset(d);
      // Sul telefono il cassetto ha fatto il suo: lasciarlo aperto significa
      // nascondere dietro un velo il report appena caricato.
      if (window.matchMedia(`(max-width: ${SOGLIA_STRETTA}px)`).matches) setRailAperto(false);
      setFiltro(null);
      setMisura(d.suggested_measure ?? "");
      setCategoria(d.suggested_category ?? "");
      setUnita(d.suggested_unit ?? "");
    } catch (e) {
      setErrore(messaggioErrore(e));
      setDataset(null);
    } finally {
      setCaricamento(false);
    }
  }

  // Il report si ricarica quando cambia una delle sue dipendenze, e le risposte
  // sorpassate le scarta l'hook: e' lo stesso principio delle "signature"
  // dell'app Streamlit — non ricalcolare l'invariato — espresso dal framework
  // invece che da una firma costruita a mano.
  const { dati: report, errore: erroreReport } = useRichiesta(
    dataset
      ? () =>
          api.report(dataset.dataset_id, {
            measure: misura,
            category: categoria,
            unit: unita,
            filtro,
          })
      : null,
    [dataset, misura, categoria, unita, filtro],
  );

  const inAttesa = caricamento || (dataset !== null && report === null && !errore && !erroreReport);
  const fig = (nome: string) =>
    report?.figures?.[nome] as Record<string, unknown> | undefined;

  // Le colonne che il BACKEND ha scelto, più il motore: è ciò che serve alle due
  // chiamate che passano dal modello. Si compone una volta perché sintesi e
  // report esecutivo devono per forza descrivere gli stessi numeri.
  const prosa: OpzioniProsa = {
    measure: report?.measure ?? undefined,
    category: report?.category ?? undefined,
    unit: report?.unit,
    ...motore,
  };

  // Il report resta quello VECCHIO finché il nuovo non arriva: cambiando dataset
  // c'è un istante in cui l'id è già del file nuovo e le colonne sono ancora del
  // precedente. In quell'istante la sintesi partiva davvero — vista nei log come
  // `overview?measure=Sales&category=Region` su un archivio di film, che quelle
  // colonne non le ha. Una chiamata al modello sprecata, una prosa costruita su
  // numeri inesistenti, e nella demo pubblica una domanda scalata dalla quota.
  //
  // Si aspetta che le colonne del report esistano DAVVERO in questo dataset.
  const reportCoerente =
    report !== null &&
    dataset !== null &&
    (!report.measure || dataset.measures.includes(report.measure)) &&
    (!report.category || dataset.categories.includes(report.category));

  return (
    <div className={`impaginazione${railAperto ? "" : " rail-chiuso"}`}>
      {/* Il bottone vive FUORI dal rail: dentro, chiudendolo, sparirebbe insieme
          a lui e non ci sarebbe piu' modo di riaprirlo. */}
      <button
        className="interruttore-rail"
        onClick={() => setRailAperto((v) => !v)}
        aria-expanded={railAperto}
        aria-controls="rail"
        title={railAperto ? "Nascondi i controlli" : "Mostra i controlli"}
      >
        {railAperto ? "‹" : "›"}
      </button>

      {/* Il velo: su un telefono toccare fuori dal cassetto lo chiude, che e'
          quello che ci si aspetta da un cassetto. E' un <button> e non un <div>
          perche' e' un comando: cosi' lo raggiunge anche chi naviga da tastiera,
          e chi usa uno screen reader sente cosa fa. Sul desktop il CSS lo
          nasconde — li' il rail non copre nulla. */}
      {railAperto && (
        <button
          className="velo-rail"
          onClick={() => setRailAperto(false)}
          aria-label="Chiudi i controlli"
        />
      )}

      <aside className="rail" id="rail" hidden={!railAperto}>
        <div className="rail-testata">
          <div>
            <h1>Natural Language Data Analyst</h1>
            <div className="sottotitolo">Interfaccia React · API FastAPI</div>
          </div>
          <Tema />
        </div>

        {/* I controlli stanno in GRUPPI: caricare un file e scegliere la misura
            sono decisioni di natura diversa, e un elenco piatto di campi uguali
            costringe a leggerli tutti per trovare quello che serve. */}
        <section className="rail-gruppo">
          <h2>Dati</h2>

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
          {/* L'azione principale si vede che lo e': prima erano tre pulsanti
              identici, e "carica i TUOI dati" — il motivo per cui l'app esiste —
              si leggeva come una delle tre opzioni di prova. */}
          <button
            className="primario"
            onClick={() => inputFile.current?.click()}
            disabled={caricamento}
          >
            Carica un file
          </button>
          <p className="nota-campo">CSV, Excel o JSON · max 25 MB</p>

          {/* L'elenco arriva da `/config` e riflette i file DAVVERO presenti,
              quindi non promette un esempio che manca. */}
          {(config?.demo_datasets ?? []).length > 0 && (
            <>
              <span className="etichetta">oppure prova con</span>
              {(config?.demo_datasets ?? []).map((d) => (
                <button
                  key={d.name}
                  className="esempio-dataset"
                  onClick={() => void accogli(api.caricaEsempio(d.name))}
                  disabled={caricamento}
                >
                  <strong>{d.label}</strong>
                  <span>{d.description}</span>
                </button>
              ))}
            </>
          )}
        </section>

        {dataset && (
          <section className="rail-gruppo">
            <h2>Report</h2>
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

            {/* `key`: cambiando dataset il componente si ricrea da zero, che e'
                il modo di React di dire "dimentica tutto" — al posto di un
                effetto che azzerava lo stato a mano. */}
            <Filtro
              key={dataset.dataset_id}
              dataset={dataset}
              filtro={filtro}
              onCambia={setFiltro}
            />

          </section>
        )}

        <section className="rail-gruppo">
          <h2>Avanzate</h2>
          {dataset && (
            /* Stessa `key`: unito un file, il pannello riparte pulito invece di
               conservare le chiavi del dataset precedente. */
            <Unione
              key={`unione-${dataset.dataset_id}`}
              dataset={dataset}
              onUnito={(nuovo) => void accogli(Promise.resolve(nuovo))}
            />
          )}
          <Modello config={config} motore={motore} onCambia={setMotore} />
        </section>
      </aside>

      <main className="spazio-lavoro">
        <h1 className="titolo">Natural Language Data Analyst</h1>
        <p className="sottotitolo-pagina">
          {dataset
            ? `${dataset.label} — ${dataset.rows.toLocaleString("it-IT")} righe · ${dataset.columns} colonne`
            : caricamento
              ? "Preparo il dataset di esempio…"
              : "Carica un file per iniziare, oppure scegli un dataset di esempio."}
        </p>

        {filtro && (
          <div className="filtro-attivo">
            {/* L'etichetta la compone `views.apply_filter`, che distingue il
                valore singolo dall'insieme: scriverla qui sarebbe una terza
                forma dello stesso filtro. */}
            <span>Filtro attivo: {report?.filter_label ?? filtro.column}</span>
            <button className="tenue" onClick={() => setFiltro(null)}>
              Togli il filtro
            </button>
          </div>
        )}

        {(errore ?? erroreReport) && (
          <div className="errore">{errore ?? erroreReport}</div>
        )}

        {!dataset && !caricamento && !errore && (
          <p className="vuoto">I numeri li calcola Pandas; il modello si limita a raccontarli.</p>
        )}

        {(dataset || caricamento) && (
          <>
            <div className="griglia-kpi">
              {report ? report.kpis.map((k, i) => <Kpi dati={k} key={i} />) : <KpiScheletro />}
            </div>

            {/* Anteprima e struttura stanno QUI, subito sotto i KPI, ma CHIUSE.
                Sono la risposta a "cosa c'è dentro questo file?", che uno si fa
                una volta sola: aperte occupavano lo schermo che serve al report,
                in fondo alla pagina bisognava cercarle. Chiuse e in alto sono a
                un clic quando servono e non ingombrano quando non servono. */}
            <Pannello titolo="Anteprima dei dati">
              {report ? (
                <Tabella righe={report.preview} massimo={10} />
              ) : (
                <div className="scheletro" style={{ height: 220 }} />
              )}
            </Pannello>

            {dataset && <Struttura dataset={dataset} />}

            <div className="scala" />

            <div className="colonne">
              <div>
                <h2 className="sezione">Report iniziale sui dati</h2>

                {dataset && reportCoerente && (
                  <Sintesi datasetId={dataset.dataset_id} opzioni={prosa} />
                )}

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
                  {/* I titoli usano le scelte che il BACKEND ha applicato, non lo
                      stato locale: se il client le lascia vuote le decide lui, sul
                      dataset filtrato, e un titolo costruito qui nominerebbe una
                      colonna diversa da quella tracciata. */}
                  <Grafico
                    titolo={`Classifica: ${report?.measure ?? "conteggio"} per ${report?.category ?? "categoria"}`}
                    figura={fig("top")}
                    onCategoria={(valore) => {
                      // Cliccare una barra filtra la pagina su quella categoria;
                      // ricliccarla toglie il filtro. È la funzione "grafici
                      // collegati" dell'app Streamlit — lì filtrava il solo
                      // andamento, qui l'intera pagina, che è quel che l'utente si
                      // aspetta dopo aver visto il filtro del rail fare lo stesso.
                      const colonna = report?.category;
                      if (!colonna) return;
                      const attivo =
                        filtro?.column === colonna &&
                        filtro.values.length === 1 &&
                        filtro.values[0] === valore;
                      setFiltro(attivo ? null : { column: colonna, values: [valore] });
                    }}
                  />
                  <Grafico
                    titolo={`Andamento di ${report?.measure ?? "conteggio"} nel tempo`}
                    figura={fig("trend")}
                  />
                </div>

                {(fig("dist") || inAttesa) && (
                  <div style={{ marginTop: 16 }}>
                    <Grafico titolo={`Distribuzione di ${report?.measure ?? ""}`} figura={fig("dist")} />
                  </div>
                )}

                {fig("corr") && (
                  <div style={{ marginTop: 16 }}>
                    <Grafico titolo="Correlazioni fra le misure" figura={fig("corr")} />
                  </div>
                )}

                {dataset && reportCoerente && (
                  <ReportEsecutivo datasetId={dataset.dataset_id} opzioni={prosa} />
                )}

                {report?.numeric_stats?.length ? (
                  <>
                    <h2 className="sezione">Statistiche delle colonne numeriche</h2>
                    <Tabella righe={report.numeric_stats} />
                  </>
                ) : null}

                {/* `dataset &&` e non `dataset!`: questo blocco si disegna anche
                    mentre il file sta caricando, quando `dataset` e' ancora null.
                    L'asserzione di non-nullita' zittiva il compilatore proprio sul
                    caso che capita davvero, e il componente esplodeva a runtime. */}
                {dataset && (
                  <Periodi
                    dataset={dataset}
                    misura={misura}
                    frequenze={config?.frequencies ?? []}
                  />
                )}


              </div>

              {dataset && (
                <Chat dataset={dataset} unita={unita} filtro={filtro} motore={motore} />
              )}
            </div>
          </>
        )}
      </main>

      <BollaProgetto esempi={config?.project_questions ?? []} />
    </div>
  );
}
