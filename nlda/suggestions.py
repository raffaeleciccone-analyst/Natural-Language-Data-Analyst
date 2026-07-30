"""
Cosa proporre all'utente che non sa da dove iniziare.

## Perché è un modulo e non due liste nei componenti

Sono frasi che orientano verso domande che l'agente gestisce bene — le stesse che
compaiono in `scripts/record_corpus.py` e `scripts/smoke.py`. Erano scritte due
volte, una per interfaccia, e **avevano già divergato**: la versione Streamlit
proponeva fino a tre domande con un ramo per il solo raggruppamento, quella React
due e senza quel ramo. Stessa domanda dell'utente ("da dove comincio?"), due
risposte diverse a seconda della pagina aperta.

Qui stanno una volta sola; le interfacce le ricevono e si limitano a mostrarle.
"""

# Domande che chi VALUTA il progetto fa davvero, e che la documentazione copre
# bene. Fisse: non dipendono dai dati caricati, ma dal progetto.
PROJECT_QUESTIONS: tuple[str, ...] = (
    "Come funziona la sandbox di sicurezza?",
    "Come evitate che l'AI inventi i numeri?",
    "Che design pattern sono stati usati?",
)

# Le frequenze del confronto tra periodi, nell'ordine in cui si mostrano. Le
# etichette SONO i valori che `nlda.periods.compare_periods` accetta: una mappa
# da etichetta a codice sarebbe una terza copia da tenere allineata.
FREQUENCIES: tuple[str, ...] = ("mese", "trimestre", "anno")


def example_questions(measure: "str | None", category: "str | None") -> list[str]:
    """
    Domande d'esempio costruite sulle colonne già scelte per il report: così i
    suggerimenti sono pertinenti al dataset caricato, non frasi fisse buone solo
    per la demo. Ripiego su due domande generiche quando il dataset non offre né
    misura né categoria.
    """
    esempi: list[str] = []
    if measure and category:
        esempi.append(f"Mostrami {measure} per {category}")
        esempi.append(f"Quali sono i 5 {category} con più {measure}?")
    if measure:
        esempi.append(f"Qual è il totale di {measure}?")
    elif category:
        esempi.append(f"Quante righe per ciascun {category}?")
    return esempi[:3] or ["Quante righe ha il dataset?", "Mostrami le prime 10 righe"]
