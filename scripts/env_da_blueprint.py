"""
Estrae da `render.yaml` le variabili d'ambiente del deploy, in formato
`--env-file` di Docker.

## Perché esiste

Il blueprint descrive come gira l'app in produzione, ma finora non lo leggeva
nessuno: i test giravano coi default del codice, la CI avviava il container
senza variabili, e il deploy usava valori che nessuno aveva mai provato.

Il buco si è visto tutto in una volta. `MEMORY_LIMIT_MB=256`, scritto nel
blueprint, faceva morire il worker della sandbox prima ancora che importasse
pandas: ogni domanda della demo pubblica falliva, mentre in locale e in CI
andava tutto verde perché lì valeva il default (1500). Il difetto non era nel
codice — era in un file di configurazione che nessun controllo apriva.

Con questo, la CI può avviare il container ESATTAMENTE come lo avvia Render, e
un valore sbagliato nel blueprint fallisce lì invece che sulla demo.

## Cosa NON esce

Le variabili marcate `sync: false` sono i segreti (la chiave del provider): nel
file non hanno un valore, e chi legge questo script non deve poterne inventare
uno. Restano fuori, ed è corretto anche per il controllo — verifica la sandbox,
che non chiama nessun modello.

Uso:
    python scripts/env_da_blueprint.py > deploy.env
    docker run --env-file deploy.env ...
"""
import sys
from pathlib import Path

import yaml

BLUEPRINT = Path(__file__).resolve().parent.parent / "render.yaml"


def variabili(percorso: Path = BLUEPRINT) -> dict[str, str]:
    """Le variabili con un valore letterale, per ogni servizio del blueprint."""
    dati = yaml.safe_load(percorso.read_text(encoding="utf-8")) or {}
    fuori: dict[str, str] = {}
    for servizio in dati.get("services", []):
        for voce in servizio.get("envVars", []):
            # `value` assente = segreto (`sync: false`) o riferimento a un altro
            # servizio: in entrambi i casi qui non c'è nulla da passare.
            if "value" in voce:
                fuori[str(voce["key"])] = str(voce["value"])
    return fuori


def main() -> int:
    if not BLUEPRINT.is_file():
        print(f"{BLUEPRINT} non trovato", file=sys.stderr)
        return 1
    trovate = variabili()
    if not trovate:
        print("nessuna variabile con valore nel blueprint", file=sys.stderr)
        return 1
    for chiave, valore in trovate.items():
        print(f"{chiave}={valore}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
