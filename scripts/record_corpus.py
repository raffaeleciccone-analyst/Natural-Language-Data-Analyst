"""
Registra un corpus di risposte REALI del modello, da rigiocare offline nei test.

Perché serve: la suite sostituisce il provider con un finto, quindi il codice che
il modello produce davvero non attraversa mai la pipeline (wrapping ->
validazione -> esecuzione). È lì che si nascondono le rotture: una guardia
aggiunta a `_wrap_chart` ha smesso di produrre grafici per la forma di codice più
frequente, e la suite è rimasta verde.

Il corpus registra l'output GREZZO del modello, prima di ogni elaborazione. I
test lo rigiocano attraverso l'intera pipeline in modo deterministico e senza
rete: se una modifica rompe il trattamento di una risposta reale, diventa rosso.

    python scripts/record_corpus.py                 # provider di default (ollama)
    python scripts/record_corpus.py --provider groq --model llama-3.3-70b-versatile

Il file prodotto (tests/fixtures/model_outputs.json) va committato. Rigenerarlo è
una scelta consapevole: cambia le risposte su cui i test si basano.
"""
import argparse
import json
from pathlib import Path

from nlda.agent import DataAgent
from nlda.loader import load_dataset

DESTINAZIONE = Path(__file__).parent.parent / "tests" / "fixtures" / "model_outputs.json"

# Domande scelte per coprire le FORME di risposta, non gli argomenti: scalare,
# aggregato, grafico esplicito, serie temporale, classifica, multi-passaggio,
# filtro. Sono le forme che la pipeline deve saper trattare.
DOMANDE = [
    "Qual è il totale delle vendite?",
    "Qual è la media delle vendite?",
    "Quali sono le vendite per regione?",
    "Mostrami le vendite per regione",
    "Mostrami il totale delle vendite",
    "Qual è l'andamento delle vendite nel tempo?",
    "Fammi un grafico a barre delle vendite per categoria",
    "Quali sono i 5 prodotti più venduti?",
    "Quanto incide ogni categoria sul totale delle vendite?",
    "Qual è il mese con più vendite?",
    "Quante righe hanno vendite superiori a 1000?",
    "Mostrami la distribuzione delle vendite",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    df = load_dataset()
    agent = DataAgent(provider=args.provider, model_name=args.model)
    system_prompt = agent._get_system_prompt(df)

    corpus = []
    for i, domanda in enumerate(DOMANDE, 1):
        wants, kind = agent._chart_intent(domanda)
        # Stessa costruzione di `ask_code`, ma si conserva l'output GREZZO.
        testo = domanda + (" (Raggruppa i dati usando as_index=False)" if wants else "")
        raw = agent._generate(system_prompt, testo)
        corpus.append({"question": domanda, "wants_chart": wants, "kind": kind,
                       "raw_code": raw})
        print(f"[{i:2}/{len(DOMANDE)}] {domanda}\n         -> {raw[:88]}")

    DESTINAZIONE.parent.mkdir(parents=True, exist_ok=True)
    DESTINAZIONE.write_text(
        json.dumps({"provider": args.provider, "model": agent.provider.model_name,
                    "cases": corpus}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf8")
    print(f"\nregistrati {len(corpus)} casi in {DESTINAZIONE}")


if __name__ == "__main__":
    main()
