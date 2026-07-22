"""
Valuta la CORRETTEZZA delle risposte, non solo che l'app non si rompa.

È l'unica verifica che gli altri livelli non danno. Un esempio reale osservato su
questo progetto: alla domanda "mostrami il totale delle vendite" il modello ha
prodotto

    result = df.groupby(['Order ID'], as_index=False)['Sales'].sum().sum()

codice valido, accettato dalla sandbox, eseguito senza errori, con tanto di
grafico. E il risultato era spazzatura: aveva concatenato gli ID come stringhe
invece di sommare le vendite. Test verdi, utente ingannato.

Qui ogni domanda ha una risposta ATTESA calcolata in Pandas sul dataset di
esempio. Il confronto è volutamente tollerante — il modello può rispondere con
uno scalare, una tabella o una frase — e cerca il valore atteso nel risultato o
nel suo riepilogo.

    python scripts/eval.py
    python scripts/eval.py --provider groq --model llama-3.3-70b-versatile

Non è un test da CI: il punteggio dipende dal modello e oscilla. È una MISURA. Un
sistema LLM non si dichiara corretto, si valuta — e quello che conta è sapere
quanto sbaglia e accorgersi quando peggiora.
"""
import argparse
import re
import sys

from nlda.agent import DataAgent
from nlda.loader import load_dataset
from nlda.results import ExecutionFailure, ExecutionSuccess
from nlda.service import AnalysisService

# (domanda, come si calcola la verità in Pandas, tipo di confronto)
CASI = [
    ("Qual è il totale delle vendite?", lambda d: d["Sales"].sum(), "numero"),
    ("Qual è la media delle vendite?", lambda d: d["Sales"].mean(), "numero"),
    ("Qual è la vendita più alta?", lambda d: d["Sales"].max(), "numero"),
    ("Quanti record ci sono nel dataset?", lambda d: len(d), "numero"),
    ("Quante regioni distinte ci sono?", lambda d: d["Region"].nunique(), "numero"),
    ("Quanti clienti distinti ci sono?", lambda d: d["Customer ID"].nunique(), "numero"),
    ("Qual è la regione con più vendite?",
     lambda d: d.groupby("Region")["Sales"].sum().idxmax(), "etichetta"),
    ("Qual è la categoria con più vendite?",
     lambda d: d.groupby("Category")["Sales"].sum().idxmax(), "etichetta"),
    ("Quanto vale il totale delle vendite nella regione West?",
     lambda d: d[d["Region"] == "West"]["Sales"].sum(), "numero"),
    ("Quante righe hanno vendite superiori a 1000?",
     lambda d: int((d["Sales"] > 1000).sum()), "numero"),
]

_NUMERO = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _numeri_nel_testo(testo: str) -> list[float]:
    """Numeri presenti in un testo, ignorando i separatori delle migliaia."""
    trovati = []
    for grezzo in _NUMERO.findall(testo.replace(",", "")):
        try:
            trovati.append(float(grezzo))
        except ValueError:
            pass
    return trovati


def _corrisponde(result: ExecutionSuccess, atteso, tipo: str) -> bool:
    """
    Confronto tollerante: il modello può rispondere con uno scalare, una tabella
    o una frase, e tutte e tre possono essere risposte corrette.
    """
    testo = f"{result.value}\n{result.summary}"
    if tipo == "etichetta":
        return str(atteso).lower() in testo.lower()

    obiettivo = float(atteso)
    tolleranza = max(abs(obiettivo) * 0.01, 0.01)  # 1%: il modello arrotonda
    if isinstance(result.value, (int, float)) and not isinstance(result.value, bool):
        return abs(float(result.value) - obiettivo) <= tolleranza
    return any(abs(n - obiettivo) <= tolleranza for n in _numeri_nel_testo(testo))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    df = load_dataset()
    service = AnalysisService(DataAgent(provider=args.provider, model_name=args.model))
    print(f"provider={args.provider} modello={service.agent.provider.model_name}\n")

    corrette = 0
    for domanda, verita, tipo in CASI:
        atteso = verita(df)
        turn = service.answer(domanda, df, explain=False)

        if isinstance(turn.result, ExecutionFailure):
            esito, dettaglio = "ERRORE  ", turn.result.kind
        elif _corrisponde(turn.result, atteso, tipo):
            esito, dettaglio = "corretta", ""
            corrette += 1
        else:
            ottenuto = str(turn.result.value).replace("\n", " ")[:52]
            esito, dettaglio = "SBAGLIATA", f"atteso {atteso} · ottenuto {ottenuto}"

        print(f"  [{esito}] {domanda}")
        if dettaglio:
            print(f"             {dettaglio}")
            print(f"             {turn.code.replace(chr(10), ' ; ')[:92]}")

    punteggio = corrette / len(CASI) * 100
    print(f"\n  PUNTEGGIO: {corrette}/{len(CASI)} ({punteggio:.0f}%)")
    print("  Il punteggio dipende dal modello: un modello piccolo sbaglia di più.")
    print("  Serve a confrontare modelli e a vedere se un cambio al prompt peggiora.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
