"""
Smoke test end-to-end contro un modello VERO: la pipeline funziona davvero?

I test della suite sostituiscono il provider con un finto e rigiocano un corpus
registrato: coprono il nostro codice, non il sistema in funzione. Qui si fa la
cosa che nessun test automatico può fare senza costi e senza non determinismo:
si pongono domande a un modello reale e si guarda cosa succede.

Non va in CI sulle pull request — è lento e dipende da un servizio esterno — ma
prima di un deploy o dopo un cambio al prompt vale i suoi due minuti.

    python scripts/smoke.py
    python scripts/smoke.py --provider groq --model llama-3.3-70b-versatile

Esce con codice diverso da zero se una domanda fallisce o se un grafico atteso
non arriva: è pensato per essere usato anche da uno script di rilascio.
"""
import argparse
import sys
import time

import pandas as pd

from nlda.agent import DataAgent
from nlda.loader import load_dataset
from nlda.results import ExecutionFailure
from nlda.service import AnalysisService

# (domanda, ci si aspetta un grafico?)
DOMANDE = [
    ("Qual è il totale delle vendite?", False),
    ("Quali sono le vendite per regione?", False),
    ("Mostrami le vendite per regione", True),
    ("Fammi un grafico a barre delle vendite per categoria", True),
    ("Qual è l'andamento delle vendite nel tempo?", True),
    ("Quali sono i 5 prodotti più venduti?", False),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default=None)
    parser.add_argument("--explain", action="store_true",
                        help="genera anche la risposta discorsiva (raddoppia le chiamate)")
    args = parser.parse_args()

    df = load_dataset()
    service = AnalysisService(DataAgent(provider=args.provider, model_name=args.model))
    print(f"provider={args.provider} modello={service.agent.provider.model_name} "
          f"dataset={len(df)} righe\n")

    problemi: list[str] = []
    for domanda, grafico_atteso in DOMANDE:
        t0 = time.monotonic()
        turn = service.answer(domanda, df, explain=args.explain)
        durata = time.monotonic() - t0

        if isinstance(turn.result, ExecutionFailure):
            problemi.append(f"{domanda!r}: {turn.result.kind} — {turn.result.message[:80]}")
            esito = f"FALLITA ({turn.result.kind})"
        else:
            ha_grafico = turn.result.fig is not None
            tabellare = isinstance(turn.result.value, (pd.DataFrame, pd.Series))
            # Si pretende il grafico solo se i dati sono graficabili: a una domanda
            # grafica con risposta scalare non si può disegnare nulla, ed è corretto.
            if grafico_atteso and tabellare and not ha_grafico:
                problemi.append(f"{domanda!r}: dati graficabili ma nessun grafico")
            esito = "ok" + (" +grafico" if ha_grafico else "")

        print(f"  [{esito:14}] {durata:5.1f}s  {domanda}")
        print(f"                    {turn.code.replace(chr(10), ' ; ')[:96]}")

    print()
    if problemi:
        print(f"{len(problemi)} PROBLEMI:")
        for p in problemi:
            print(f"  - {p}")
        return 1
    print(f"tutte e {len(DOMANDE)} le domande hanno prodotto un risultato valido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
