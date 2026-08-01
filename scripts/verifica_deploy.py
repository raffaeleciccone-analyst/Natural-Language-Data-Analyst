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
import time
import urllib.error
import urllib.request

URL_PREDEFINITO = "https://nlda.onrender.com"

# Generoso di proposito: sul piano gratuito il servizio si spegne dopo 15 minuti
# di inattività e la prima richiesta aspetta l'avvio del container.
#
# Il risveglio è a tentativi e non a singola attesa lunga perché mentre il
# container parte Render non tiene la connessione aperta in silenzio: la chiude,
# o risponde 502/503 dal suo proxy. Un solo tentativo da tre minuti si arrende
# alla prima di queste — ed è già successo, il 1º agosto 2026.
BUDGET_RISVEGLIO = 420
ATTESA_TENTATIVO = 60
PAUSA_TENTATIVI = 5
ATTESA_DOMANDA = 240


class Fallito(Exception):
    """Un controllo non è passato. Il messaggio è già leggibile da un umano."""


def _chiama(url: str, corpo: dict | None = None, timeout: int = 60) -> tuple[dict, float]:
    """Chiama l'API e restituisce (json, secondi). Solleva `Fallito` con la causa."""
    dati = json.dumps(corpo).encode() if corpo is not None else None
    intestazioni = {"Content-Type": "application/json"} if dati else {}
    richiesta = urllib.request.Request(url, data=dati, headers=intestazioni,
                                       method="POST" if dati is not None else "GET")
    inizio = time.perf_counter()
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as r:  # nosec B310 — https
            return json.loads(r.read()), time.perf_counter() - inizio
    except urllib.error.HTTPError as e:
        # `from None`: il messaggio dice gia' tutto (codice e corpo della
        # risposta), e la traccia di urllib qui non aggiunge nulla di leggibile.
        raise Fallito(
            f"{url} ha risposto {e.code}: {e.read()[:200].decode(errors='replace')}") from None
    except Exception as e:  # noqa: BLE001 — rete: qualunque motivo va riportato
        raise Fallito(f"{url} non raggiungibile: {e}") from e


def _sveglia(url: str) -> tuple[dict, float, int]:
    """Insiste finché il servizio non risponde. Restituisce (json, secondi, tentativi).

    Solleva `Fallito` con l'ultima causa se il budget finisce: a quel punto non è
    più un container che parte piano, è una demo giù.
    """
    inizio = time.perf_counter()
    for tentativo in range(1, 1_000):
        try:
            salute, _ = _chiama(url, timeout=ATTESA_TENTATIVO)
            return salute, time.perf_counter() - inizio, tentativo
        except Fallito as e:
            trascorso = time.perf_counter() - inizio
            if trascorso + ATTESA_TENTATIVO > BUDGET_RISVEGLIO:
                raise Fallito(f"{e} — non si è svegliato in {trascorso:.0f}s "
                              f"({tentativo} tentativi)") from None
            _riga("··", f"tentativo {tentativo} a vuoto dopo {trascorso:.0f}s, riprovo")
            time.sleep(PAUSA_TENTATIVI)
    raise AssertionError("irraggiungibile: il budget scade molto prima")  # pragma: no cover


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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=URL_PREDEFINITO, help="base del servizio da verificare")
    p.add_argument("--senza-modello", action="store_true",
                   help="salta la domanda: nessun consumo di quota")
    args = p.parse_args()

    base = args.url.rstrip("/")
    print(f"Verifica di {base}")
    try:
        problemi = verifica(base, con_modello=not args.senza_modello)
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
