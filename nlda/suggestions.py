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
from collections.abc import Sequence

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


def example_questions(
    measure: "str | None",
    category: "str | None",
    *,
    date_column: "str | None" = None,
    date_span_years: "float | None" = None,
    other_measures: "Sequence[str]" = (),
) -> list[str]:
    """
    Domande d'esempio costruite sulle colonne del dataset caricato.

    ## Il criterio: NON chiedere ciò che è già a schermo

    Le prime tre erano "Mostrami {misura} per {categoria}", "Quali sono i 5
    {categoria} con più {misura}?" e "Qual è il totale di {misura}?". Tutte e tre
    rispondevano a qualcosa che il report mostra già: le prime due sono il grafico
    della classifica, la terza è **letteralmente il primo KPI in cima alla
    pagina**. Chi le provava riceveva un numero che aveva sotto gli occhi, e ne
    concludeva che la chat non serve a nulla.

    Ora ciascuna mostra qualcosa che il report NON fa: il massimo nel tempo (che
    il grafico dell'andamento lascia stimare a occhio), la media invece della
    somma, il legame fra due misure, le righe estreme in dettaglio.

    ## E la grammatica

    "Quali sono i 5 Region con più Sales?" è sgrammaticato: il nome della colonna
    è singolare e inglese, l'articolo italiano è plurale. Le formulazioni qui
    reggono qualunque nome di colonna perché lo trattano come un'ETICHETTA
    ("i 5 valori di Region") invece di declinarlo.
    """
    esempi: list[str] = []

    # Il tempo: è la domanda più naturale davanti a dei dati, e il report mostra
    # una curva su cui il massimo si stima a occhio invece di leggerlo.
    #
    # La granularità la decidono i DATI: "in che mese" su un archivio di film che
    # copre ottant'anni è una domanda che nessuno si farebbe — la stessa
    # insensatezza delle domande che questa funzione ha sostituito. La soglia è a
    # tre anni: sotto, i mesi sono ancora una lettura utile; sopra, il confronto
    # che interessa è fra anni.
    periodo = "anno" if (date_span_years or 0) > 3 else "mese"
    if measure and date_column:
        esempi.append(f"In che {periodo} {measure} è stato più alto?")
    elif date_column:
        esempi.append(f"Quante righe per ogni {periodo} di {date_column}?")

    # La MEDIA, non il totale: il totale è il primo KPI.
    if measure and category:
        esempi.append(f"Qual è la media di {measure} per ogni valore di {category}?")

    # Il legame fra due misure: la heatmap dice che esiste, non cosa significa.
    seconda = _misura_da_confrontare(measure, other_measures)
    if measure and seconda:
        esempi.append(f"C'è relazione tra {measure} e {seconda}?")

    # Le righe estreme: il report aggrega, qui si guardano i record singoli.
    if measure:
        esempi.append(f"Mostrami le 10 righe con {measure} più alto")

    if category:
        esempi.append(f"Quanti valori distinti ha {category}?")

    return esempi[:3] or ["Quante righe ha il dataset?", "Mostrami le prime 10 righe"]


def _misura_da_confrontare(principale: "str | None",
                           candidate: "Sequence[str]") -> "str | None":
    """
    La misura da mettere accanto a quella principale in una domanda sul legame
    fra due grandezze.

    Non la prima disponibile: su un archivio di film sarebbe "Worldwide Gross"
    accanto a "US Gross", cioè due colonne che misurano quasi la stessa cosa —
    una correlazione vicina a 1 che non insegna nulla. Si preferisce la candidata
    che condivide MENO parole con la principale, che è il modo più semplice di
    dire "qualcosa di diverso" senza guardare i dati.

    A parità di sovrapposizione vince l'ordine originale, che mette avanti le
    misure più importanti.
    """
    if not principale:
        return None
    parole = set(principale.lower().split())
    diverse = [c for c in candidate if c != principale]
    if not diverse:
        return None
    return min(diverse, key=lambda c: len(parole & set(c.lower().split())))
