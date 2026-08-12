"""
Verifica che la demo pubblica funzioni DAVVERO, interrogandola dall'esterno.

## Perché non basta la CI

La CI prova l'immagine e, da poco, anche il container avviato con le variabili di
`render.yaml`. Restano fuori due cose che solo il servizio vero sa:

* **se l'host applica davvero quelle variabili.** `EXEC_TIMEOUT=60` è rimasto nel
  blueprint per giorni senza mai raggiungere l'app — Render non aggiunge a un
  servizio già creato le chiavi nuove — e la demo continuava a rispondere "12s"
  mentre in CI era tutto verde;
* **come si comporta su quell'hardware.** Sul piano gratuito la CPU è 0,1 vCPU
  condivisa: il worker della sandbox ci mette un ordine di grandezza in più che
  su un runner, ed è esattamente lì che il progetto si è rotto tre volte.

Questo script chiude il divario nell'unico modo possibile: chiedendo al servizio.

## Cosa costa

**Una** domanda al modello per esecuzione, cioè una sull'intera quota giornaliera
condivisa. Con `--senza-modello` non ne costa nessuna e si ferma ai controlli
deterministici, utile per lanciarlo spesso.

Uso:
    python scripts/verifica_deploy.py                    # https://nlda.onrender.com
    python scripts/verifica_deploy.py --url https://... --senza-modello
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote

URL_PREDEFINITO = "https://nlda.onrender.com"

# Generoso di proposito: sul piano gratuito il servizio si spegne dopo 15 minuti
# di inattività e la prima richiesta aspetta l'avvio del container.
#
# Il risveglio è a tentativi e non a singola attesa lunga perché mentre il
# container parte Render non tiene la connessione aperta in silenzio: la chiude,
# o risponde 502/503 dal suo proxy. Un solo tentativo da tre minuti si arrende
# alla prima di queste — ed è già successo, il 1º agosto 2026.
#
# Il budget era 420 s: il 9 agosto 2026 il controllo giornaliero è fallito
# esaurendolo, e il giorno dopo — stesso commit, stesso servizio — è tornato
# verde da solo. Un risveglio lento su 0,1 vCPU condivisa non è una demo giù, e
# un fallimento che non chiede di fare nulla insegna a ignorare quelli veri. Il
# tetto del job resta 20 minuti, quindi lo spazio c'era già.
BUDGET_RISVEGLIO = 600
ATTESA_TENTATIVO = 60
PAUSA_TENTATIVI = 5
# Sotto questa soglia un tentativo non è più un tentativo: il timeout scadrebbe
# mentre il container sta ancora rispondendo, e conterebbe come un rifiuto.
ATTESA_MINIMA = 15
ATTESA_DOMANDA = 240


class Fallito(Exception):
    """Un controllo non è passato. Il messaggio è già leggibile da un umano."""


def _chiama(url: str, corpo: dict | None = None, timeout: float = 60) -> tuple[dict, float]:
    """
    Chiama l'API e restituisce (json, secondi). Solleva `Fallito` con la causa.

    La richiesta la costruisce `_prova` (definita piu' sotto, accanto agli altri
    aiutanti delle difese): qui si aggiunge solo la POLITICA — un codice diverso
    da 200 e' un guasto, non un esito da controllare. Prima `Request`, `urlopen`
    e la cattura degli errori erano scritti due volte, cioe' due punti in cui
    aggiungere un header o un proxy, e uno da dimenticare.
    """
    dati = json.dumps(corpo).encode() if corpo is not None else None
    inizio = time.perf_counter()
    stato, testo = _prova(url, dati,
                          {"Content-Type": "application/json"} if dati else {}, timeout)
    if stato == 200:
        return json.loads(testo), time.perf_counter() - inizio
    # Stato 0 = non ha risposto affatto, e `_prova` ha gia' messo il perche' nel
    # corpo; altrimenti si riporta il codice con l'inizio della risposta.
    raise Fallito(f"{url} {testo}" if stato == 0
                  else f"{url} ha risposto {stato}: {testo[:200]}")


def _sveglia(url: str) -> tuple[dict, float, int]:
    """Insiste finché il servizio non risponde. Restituisce (json, secondi, tentativi).

    Solleva `Fallito` con l'ultima causa se il budget finisce: a quel punto non è
    più un container che parte piano, è una demo giù.
    """
    inizio = time.perf_counter()
    for tentativo in range(1, 1_000):
        # L'ultimo tentativo si accorcia per stare nel budget invece di essere
        # saltato. La guardia era `trascorso + ATTESA_TENTATIVO > BUDGET`, che si
        # arrendeva con un tentativo intero di anticipo: 385 s spesi sui 420
        # dichiarati, cioè 35 s di attesa che il servizio non ha mai avuto.
        resto = BUDGET_RISVEGLIO - (time.perf_counter() - inizio)
        try:
            salute, _ = _chiama(url, timeout=min(ATTESA_TENTATIVO, resto))
            return salute, time.perf_counter() - inizio, tentativo
        except Fallito as e:
            trascorso = time.perf_counter() - inizio
            if trascorso + PAUSA_TENTATIVI + ATTESA_MINIMA > BUDGET_RISVEGLIO:
                raise Fallito(f"{e} — non si è svegliato in {trascorso:.0f}s "
                              f"({tentativo} tentativi)") from None
            _riga("··", f"tentativo {tentativo} a vuoto dopo {trascorso:.0f}s, riprovo")
            time.sleep(PAUSA_TENTATIVI)
    raise AssertionError("irraggiungibile: il budget scade molto prima")  # pragma: no cover


def _prova(url: str, corpo: bytes | None = None,
           intestazioni: dict | None = None, timeout: float = 120) -> tuple[int, str]:
    """
    Come `_chiama`, ma un 4xx è un ESITO da controllare, non un guasto.

    Le difese qui sotto si verificano proprio dai codici di errore: un 400 con il
    messaggio giusto è il comportamento corretto, e sollevare su quello renderebbe
    impossibile distinguerlo da un servizio rotto.
    """
    richiesta = urllib.request.Request(url, data=corpo, headers=intestazioni or {},
                                       method="POST" if corpo is not None else "GET")
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as r:  # nosec B310 — https
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # noqa: BLE001 — rete: qualunque motivo va riportato
        return 0, f"non raggiungibile: {e}"


def _carica(base: str, nome: str, dati: bytes) -> tuple[int, str]:
    """Carica un file come farebbe un browser. Multipart scritto a mano: questo
    script non deve avere dipendenze da installare per girare in CI."""
    bordo = f"----verifica{uuid.uuid4().hex}"
    corpo = (f"--{bordo}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{nome}\"\r\nContent-Type: text/csv\r\n\r\n").encode() \
        + dati + f"\r\n--{bordo}--\r\n".encode()
    return _prova(f"{base}/api/dataset", corpo,
                  {"Content-Type": f"multipart/form-data; boundary={bordo}"})


def _dettaglio(corpo: str) -> str:
    """Il messaggio per l'utente dentro una risposta d'errore dell'API."""
    try:
        return str(json.loads(corpo).get("detail", corpo))
    except ValueError:
        return corpo


def _riga(esito: str, testo: str, secondi: float | None = None) -> None:
    tempo = f"  ({secondi:.1f}s)" if secondi is not None else ""
    print(f"  {esito}  {testo}{tempo}")


def verifica(base: str, con_modello: bool) -> list[str]:
    """Esegue i controlli e restituisce l'elenco dei problemi trovati."""
    problemi: list[str] = []

    # 1. È vivo (e possibilmente si sta svegliando).
    salute, t, tentativi = _sveglia(f"{base}/api/health")
    if salute.get("status") != "ok":
        problemi.append(f"/api/health non dice ok: {salute}")
    _riga("OK", "il servizio risponde", t)
    if t > 20 or tentativi > 1:
        _riga("··", "era spento: la prima visita di un utente aspetta altrettanto")

    # 2. La configurazione è quella che ci si aspetta da una demo pubblica.
    config, _ = _chiama(f"{base}/api/config")
    if not config.get("demo_mode"):
        problemi.append("demo_mode è spento: le domande non hanno tetto e le paga il manutentore")
    if not config.get("demo_datasets"):
        problemi.append("nessun dataset di esempio: chi arriva non ha nulla da provare")
    _riga("OK", f"demo attiva, {config.get('max_questions')} domande a testa, "
                f"esempi: {[d['name'] for d in config.get('demo_datasets', [])]}")

    # 3. Il report: tutto Pandas, nessun modello. Se fallisce qui, non è l'LLM.
    dataset, t = _chiama(f"{base}/api/dataset/demo", corpo={})
    report, t2 = _chiama(f"{base}/api/dataset/{dataset['dataset_id']}/report")
    if not report.get("kpis"):
        problemi.append("il report non produce KPI")
    _riga("OK", f"report calcolato: {len(report.get('kpis', []))} KPI, "
                f"{len(report.get('figures', {}))} grafici", t + t2)

    # 4. La domanda vera: e' il percorso che si e' rotto tre volte.
    if not con_modello:
        _riga("··", "domanda al modello saltata (--senza-modello)")
        return problemi

    risposta, t = _chiama(f"{base}/api/ask", timeout=ATTESA_DOMANDA, corpo={
        "dataset_id": dataset["dataset_id"],
        "question": "Quante righe ha il dataset?",
        "explain": False,   # basta il codice: la narrazione costerebbe una seconda chiamata
    })
    if risposta.get("ok"):
        _riga("OK", f"domanda eseguita: {risposta.get('code', '')[:60]}", t)
    else:
        causa = risposta.get("failure_kind")
        _riga("NO", f"domanda fallita ({causa}): {risposta.get('message', '')[:120]}", t)
        problemi.append(
            f"la chat non funziona sulla demo: {causa}. "
            + {"timeout": "l'esecuzione non rientra nel tempo concesso",
               "runtime": "il worker della sandbox muore (spesso: cap di memoria troppo basso)",
               "security": "il codice generato viene rifiutato dal validatore",
               }.get(str(causa), "vedi il messaggio sopra"))
    return problemi


def _csv_di_numeri(righe: int, colonne: int) -> bytes:
    """
    Un CSV leggero su disco e pesante in memoria: due byte per cella diventano
    otto. È la forma che ha ucciso la demo il 5 agosto 2026 — dentro `MAX_ROWS` e
    dentro `MAX_COLUMNS`, e fuori da qualunque memoria disponibile.
    """
    intestazione = ",".join(f"c{i}" for i in range(colonne))
    riga = ",".join("7" for _ in range(colonne))
    return f"{intestazione}\n".encode() + f"{riga}\n".encode() * righe


def difese(base: str) -> list[str]:
    """
    Le difese che il servizio deve opporre a un input sbagliato, provate da fuori.

    Perché non bastano i test: ognuna di queste difese ha un test che la copre, e
    tutti erano verdi mentre in produzione la difesa non c'era — perché dipendeva
    da una variabile che l'host non aveva applicato, o girava su un container con
    un decimo della memoria. La CI prova il CODICE; questo prova il SERVIZIO.

    Nessun modello viene chiamato: non costa quota e si può lanciare a ogni
    deploy. Ciò che resta fuori è dichiarato in coda alla funzione.
    """
    problemi: list[str] = []

    def controlla(nome: str, ok: bool, dettaglio: str = "") -> None:
        _controlla(problemi, nome, ok, dettaglio)

    # 1. File che non sono quello che dicono di essere. Un caricamento che RIESCE
    #    sul file sbagliato è peggio di uno che fallisce: produce numeri con l'aria
    #    di essere giusti.
    for nome, contenuto, atteso in [
        ("un PDF travestito da .csv", b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n", "pdf"),
        ("uno ZIP travestito da .csv", b"PK\x03\x04" + b"\x00" * 60, "zip"),
        ("un file vuoto", b"", "vuot"),
        ("un file con la sola intestazione", b"regione,vendite\n", "intestazione"),
        ("una riga con più campi dell'intestazione",
         b"regione,vendite\nNord,100\nSud,200,999\n", "riga 3"),
    ]:
        stato, corpo = _carica(base, "prova.csv", contenuto)
        messaggio = _dettaglio(corpo)
        controlla(f"{nome} → 400 che dice cosa non va",
                  stato == 400 and atteso.lower() in messaggio.lower(),
                  f"stato {stato}: {messaggio}")

    # 2. Il tetto di MEMORIA, che è ciò che tiene in vita il processo.
    problemi += _difese_sulla_memoria(base)

    # 3. Parametri sbagliati: 400 con un messaggio utile, non 500. Il 500 dice "il
    #    servizio è guasto"; qui sta benissimo, ed è la richiesta a essere sbagliata.
    dataset, _ = _chiama(f"{base}/api/dataset/demo", corpo={})
    did = dataset["dataset_id"]
    categoria = (dataset.get("categories") or [""])[0]
    for nome, percorso in [
        ("una misura testuale", f"/api/dataset/{did}/report?measure={quote(categoria)}"),
        ("una misura inesistente", f"/api/dataset/{did}/report?measure=Inventata"),
        ("una categoria inesistente", f"/api/dataset/{did}/report?category=Inventata"),
    ]:
        stato, corpo = _prova(f"{base}{percorso}")
        controlla(f"{nome} → 400", stato == 400, f"stato {stato}: {_dettaglio(corpo)}")

    # 4. L'unione che moltiplica le righe non fallisce: gonfia i totali in silenzio.
    _, sinistra = _carica(base, "ordini.csv", b"ordine,totale\nA,100\nB,200\n")
    _, destra = _carica(base, "righe.csv", b"ordine,prodotto\nA,penna\nA,quaderno\nB,gomma\n")
    try:
        corpo_join = json.dumps({
            "left_id": json.loads(sinistra)["dataset_id"],
            "right_id": json.loads(destra)["dataset_id"],
            "left_on": "ordine", "right_on": "ordine", "how": "inner"}).encode()
        stato, corpo = _prova(f"{base}/api/dataset/join", corpo_join,
                              {"Content-Type": "application/json"})
        avvisi = json.loads(corpo).get("warnings") if stato == 200 else None
        controlla("un'unione che duplica le righe avvisa", bool(avvisi),
                  f"stato {stato}: {corpo[:160]}")
    except (ValueError, KeyError) as e:
        controlla("un'unione che duplica le righe avvisa", False, f"risposta inattesa: {e}")

    # Cosa NON copre, per scelta: la quota. Verificarla richiede domande vere al
    # modello — cioè budget speso a ogni esecuzione — e un'identità di visitatore
    # pulita, che si ottiene solo falsificando `X-Forwarded-For`. Si è verificata a
    # mano quando il tetto è stato messo, e la difendono i test di `test_api_quota`.
    return problemi


def _controlla(problemi: list[str], nome: str, ok: bool, dettaglio: str = "") -> None:
    """Stampa l'esito di un controllo e, se è andato male, lo aggiunge all'elenco."""
    _riga("OK" if ok else "NO", nome)
    if not ok:
        problemi.append(f"{nome} — {dettaglio[:160]}")


def _difese_sulla_memoria(base: str) -> list[str]:
    """
    Il tetto di memoria: il file di prova si tara sul tetto DICHIARATO.

    Sta in una funzione sua perché è l'unico controllo che deve prima chiedere
    come è configurato il servizio e poi decidere se ha senso eseguirsi: cinque
    variabili di calcolo che, dentro `difese`, restavano vive per tutti i blocchi
    successivi.

    Sotto un tetto largo (tipico in sviluppo) il file verrebbe accettato, e non è
    un difetto — è un'altra installazione. In quel caso si dichiara saltato.
    """
    problemi: list[str] = []
    config, _ = _chiama(f"{base}/api/config")
    tetto = int(config.get("max_dataset_ram_mb", 0))

    def controlla(nome: str, ok: bool, dettaglio: str = "") -> None:
        _controlla(problemi, nome, ok, dettaglio)

    # Otto byte per cella in memoria contro i due su disco: per sforare il tetto
    # bisogna spedire un quarto della sua dimensione. Si punta a 1,25 volte e non
    # al doppio perché il file va caricato per davvero a ogni esecuzione, e la
    # stima del servizio sbaglia di circa il 5%: il margine basta, il traffico si
    # dimezza.
    oltre = 1.25
    colonne = 100
    da_spedire_mb = tetto * oltre / 4
    if tetto and da_spedire_mb > config.get("max_upload_mb", 25):
        # Su un'installazione con memoria abbondante nessun file ammesso
        # dall'upload può sforare il tetto: il controllo non ha modo di dire nulla,
        # e fingere un esito sarebbe peggio che dichiararlo. Provando questo caso
        # lo script generava un file da 1,3 GB e riportava un 413 come se fosse
        # il tetto a mancare.
        _riga("··", f"tetto di memoria ({tetto} MB) più alto di quanto un caricamento "
                    f"possa raggiungere (max {config.get('max_upload_mb')} MB): "
                    f"controllo saltato")
    elif tetto:
        righe = int(tetto * oltre * 1024 * 1024 / (colonne * 8))
        dati = _csv_di_numeri(righe, colonne)
        stato, corpo = _carica(base, "pesante.csv", dati)
        controlla(f"un dataset da ~{round(tetto * oltre)} MB in memoria → rifiutato "
                  f"(tetto dichiarato: {tetto} MB, file inviato: {len(dati) / (1024 * 1024):.0f} MB)",
                  stato == 400 and "memoria" in _dettaglio(corpo).lower(),
                  f"stato {stato}: {_dettaglio(corpo)}")
        salute, _ = _chiama(f"{base}/api/health")
        controlla("…e il servizio è ancora vivo dopo il rifiuto",
                  salute.get("status") == "ok", str(salute))
    else:
        _riga("··", "tetto di memoria non dichiarato da /api/config: controllo saltato")

    return problemi


def main() -> int:
    # I nomi dei controlli contengono `→` e gli esiti `··`, e la console di
    # Windows è cp1252: senza questa riga lo script muore con UnicodeEncodeError
    # sul primo esito stampato, prima di dire alcunché sul servizio. In CI non si
    # vedrà mai — il runner è UTF-8 — ed è esattamente perché va scritto qui: chi
    # esegue a mano lo fa dal proprio portatile, di solito quando la demo è giù.
    # `reconfigure` esiste solo su un TextIOWrapper: sotto cattura (pytest) lo
    # stream è un altro oggetto e non c'è nulla da riconfigurare.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=URL_PREDEFINITO, help="base del servizio da verificare")
    p.add_argument("--senza-modello", action="store_true",
                   help="salta la domanda: nessun consumo di quota")
    p.add_argument("--difese", action="store_true",
                   help="prova anche le difese su input sbagliati (nessun consumo di quota)")
    args = p.parse_args()

    base = args.url.rstrip("/")
    print(f"Verifica di {base}")
    try:
        problemi = verifica(base, con_modello=not args.senza_modello)
        if args.difese:
            print("\nLe difese su input sbagliati:")
            problemi += difese(base)
    except Fallito as e:
        print(f"  NO  {e}")
        return 1

    print()
    if problemi:
        print(f"{len(problemi)} problema/i:")
        for x in problemi:
            print(f"  - {x}")
        return 1
    print("La demo pubblica funziona.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
