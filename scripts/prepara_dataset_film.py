"""
Prepara `data/films.json` dal dataset pubblico di vega-datasets.

## Perché il file non è quello originale

L'originale copre **1928-2046** — sì, 2046: ventuno film hanno l'anno a due
cifre ribaltato nel secolo sbagliato dalla fonte, e "Duel in the Sun" (1946)
risulta uscito nel 2046. Su un arco simile il grafico dell'andamento è una riga
piatta lunga settant'anni seguita da un'esplosione, e il confronto tra periodi
non dice nulla: il dataset di esempio deve mostrare cosa sa fare l'app, non
metterla alla prova su dati sporchi.

Si tiene quindi la finestra **2000-2009**: dieci anni pieni, fra 130 e 220 film
l'uno, che è una densità in cui mese, trimestre e anno hanno tutti senso. Le date
impossibili restano fuori da sole, perché cadono tutte dopo il 2015.

Nessun altro campo viene toccato: incassi, budget, voti e generi sono quelli
originali. Il sottoinsieme è dichiarato nel README e in `nlda/demo_data.py`.

Uso (serve la rete):
    python scripts/prepara_dataset_film.py
"""
import json
import sys
import urllib.request
from pathlib import Path

SORGENTE = "https://raw.githubusercontent.com/vega/vega-datasets/main/data/movies.json"
DESTINAZIONE = Path(__file__).resolve().parent.parent / "data" / "films.json"
DAL, AL = 2000, 2009


def anno(record: dict) -> int | None:
    """L'anno di uscita, o None se la data manca o non finisce con quattro cifre."""
    coda = (record.get("Release Date") or "")[-4:]
    return int(coda) if coda.isdigit() else None


def main() -> int:
    print(f"scarico {SORGENTE}")
    with urllib.request.urlopen(SORGENTE, timeout=60) as r:  # nosec B310 — URL fisso, https
        completo = json.loads(r.read())

    tenuti = [f for f in completo if (a := anno(f)) is not None and DAL <= a <= AL]
    if not tenuti:
        print("nessun film nella finestra scelta: la fonte e' cambiata?", file=sys.stderr)
        return 1

    DESTINAZIONE.write_text(
        json.dumps(tenuti, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"scritto {DESTINAZIONE.name}: {len(tenuti)} film su {len(completo)} "
          f"({DAL}-{AL}), {DESTINAZIONE.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
