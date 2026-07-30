"""
Genera i tipi TypeScript del frontend dallo schema OpenAPI dell'API.

## Perche' non `openapi-typescript`

E' lo strumento standard per questo lavoro, ed e' stato la prima scelta. Poi
`npm audit` ha riportato **4 vulnerabilita' di gravita' alta** nella sua catena di
dipendenze (@redocly/openapi-core -> minimatch -> brace-expansion), che
`npm audit fix` non risolve perche' sono bloccate da un vincolo dello strumento
stesso. Sono di sola build, quindi il rischio pratico e' minimo — ma questo
progetto fa girare `pip-audit` e `bandit` in CI, e tenere quattro vulnerabilita'
note per generare un file di tipi sarebbe incoerente con quello che dichiara.

Il costo dell'alternativa e' basso perche' lo schema NON e' arbitrario: e' il
nostro, prodotto da modelli Pydantic che controlliamo, e usa un sottoinsieme
ristretto di OpenAPI. Non serve un generatore universale, serve questo. Se un
domani lo schema usasse costrutti piu' esotici (allOf, discriminatori, ricorsione),
la scelta giusta tornerebbe a essere lo strumento di terze parti.

Vantaggio collaterale: gira con il resto di Python, quindi la CI puo' verificare
che i tipi committati siano ALLINEATI all'API senza installare Node.

Uso:
    python scripts/genera_tipi_ts.py                 # scrive il file
    python scripts/genera_tipi_ts.py --verifica      # esce 1 se e' disallineato
"""
import argparse
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
DESTINAZIONE = RADICE / "frontend" / "src" / "api" / "types.ts"

INTESTAZIONE = """\
// FILE GENERATO — non modificare a mano.
// Sorgente: schema OpenAPI dell'API (nlda/api/models.py).
// Rigenera con:  python scripts/genera_tipi_ts.py
//
// Se questo file e i modelli Pydantic divergono, il compilatore TypeScript se ne
// accorge: e' il motivo per cui i tipi si generano invece di riscriverli a mano.

"""

# Tipi primitivi di JSON Schema -> TypeScript.
_PRIMITIVI = {"string": "string", "integer": "number", "number": "number",
              "boolean": "boolean", "null": "null"}


def _nome_ref(ref: str) -> str:
    """'#/components/schemas/AskResponse' -> 'AskResponse'."""
    return ref.rsplit("/", 1)[-1]


def tipo_ts(schema: dict) -> str:
    """Traduce un nodo di JSON Schema nel tipo TypeScript corrispondente."""
    if "$ref" in schema:
        return _nome_ref(schema["$ref"])

    # anyOf con null e' il modo in cui Pydantic esprime `X | None`.
    if "anyOf" in schema:
        parti = [tipo_ts(s) for s in schema["anyOf"]]
        # 'unknown | null' si semplifica in 'unknown': unknown include gia' null.
        if "unknown" in parti:
            return "unknown"
        return " | ".join(dict.fromkeys(parti))

    if "enum" in schema:
        return " | ".join(f'"{v}"' for v in schema["enum"])

    tipo = schema.get("type")
    if tipo == "array":
        interno = tipo_ts(schema.get("items", {}))
        # Le union vanno parentesizzate: (A | B)[] non A | B[].
        return f"({interno})[]" if "|" in interno else f"{interno}[]"
    if tipo == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict) and extra:
            return f"Record<string, {tipo_ts(extra)}>"
        return "Record<string, unknown>"
    if tipo in _PRIMITIVI:
        return _PRIMITIVI[tipo]
    return "unknown"


def _commento(schema: dict, rientro: str) -> str:
    """
    La `description` dei campi Pydantic diventa un commento leggibile nell'editor.

    I docstring di questo progetto sono su piu' righe: senza l'asterisco di
    continuazione il blocco resta un commento valido, ma l'editor non lo riconosce
    come JSDoc e non lo mostra nel tooltip — che e' tutto il motivo per cui lo si
    porta fin qui.
    """
    testo = schema.get("description", "").strip()
    if not testo:
        return ""
    righe = [r.strip() for r in testo.splitlines() if r.strip()]
    if len(righe) == 1:
        return f"{rientro}/** {righe[0]} */\n"
    corpo = "".join(f"{rientro} * {r}\n" for r in righe)
    return f"{rientro}/**\n{corpo}{rientro} */\n"


def interfaccia(nome: str, schema: dict) -> str:
    """Un modello Pydantic -> un'interfaccia TypeScript."""
    righe = [_commento(schema, ""), f"export interface {nome} {{\n"]
    obbligatori = set(schema.get("required", []))
    for campo, sotto in schema.get("properties", {}).items():
        righe.append(_commento(sotto, "  "))
        opzionale = "" if campo in obbligatori else "?"
        righe.append(f"  {campo}{opzionale}: {tipo_ts(sotto)};\n")
    righe.append("}\n")
    return "".join(righe)


def genera() -> str:
    # Import qui e non in cima: lo script deve poter mostrare --help anche in un
    # ambiente senza FastAPI installato (e' un extra opzionale del progetto).
    from nlda.api.app import app

    schemi = app.openapi().get("components", {}).get("schemas", {})
    pezzi = [INTESTAZIONE]
    for nome in sorted(schemi):
        # HTTPValidationError e ValidationError le genera FastAPI per i 422: al
        # frontend non servono, gestisce gli errori con la nostra ErrorResponse.
        # `Body_*` e' il modello che FastAPI genera per gli upload multipart: al
        # client dichiarerebbe `file: string`, che e' falso — dal browser si manda
        # un `File` dentro una `FormData`. Meglio nessun tipo che uno sbagliato.
        if nome in ("HTTPValidationError", "ValidationError") or nome.startswith("Body_"):
            continue
        pezzi.append(interfaccia(nome, schemi[nome]))
        pezzi.append("\n")
    return "".join(pezzi).rstrip() + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verifica", action="store_true",
                   help="non scrive: esce con 1 se il file committato e' disallineato")
    args = p.parse_args()

    atteso = genera()
    if args.verifica:
        if not DESTINAZIONE.exists():
            print(f"MANCA {DESTINAZIONE.relative_to(RADICE)} — rigenera i tipi.")
            return 1
        if DESTINAZIONE.read_text(encoding="utf-8") != atteso:
            print(f"{DESTINAZIONE.relative_to(RADICE)} NON e' allineato all'API.\n"
                  "Rigenera con: python scripts/genera_tipi_ts.py")
            return 1
        print("i tipi TypeScript sono allineati all'API")
        return 0

    DESTINAZIONE.parent.mkdir(parents=True, exist_ok=True)
    DESTINAZIONE.write_text(atteso, encoding="utf-8")
    n = atteso.count("export interface")
    print(f"scritto {DESTINAZIONE.relative_to(RADICE)}: {n} interfacce")
    return 0


if __name__ == "__main__":
    sys.exit(main())
