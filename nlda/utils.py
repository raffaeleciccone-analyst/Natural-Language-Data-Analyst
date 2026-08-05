"""Utility condivise tra i moduli del pacchetto (evita duplicazioni)."""
import re
import warnings

import pandas as pd


def to_datetime_quiet(values, *, dayfirst: bool = False) -> pd.Series:
    """
    `pd.to_datetime(errors='coerce')` senza il rumore di 'Could not infer format'.

    Il loader prova a interpretare come data OGNI colonna testuale (per rilevare
    quali lo sono) e `compare_periods` riceve talvolta date ancora in stringa: in
    entrambi i casi il formato è ignoto e il fallback per-elemento di pandas è
    VOLUTO. L'avviso che pandas emette è quindi solo rumore — circa uno per colonna
    all'avvio — e va spento qui, dove la scelta è deliberata, non altrove.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Could not infer format",
                                category=UserWarning)
        return pd.to_datetime(values, errors="coerce", dayfirst=dayfirst)

# Convenzione numerica italiana (separatore migliaia '.', decimale ','), usata sia
# da fmt_num sia dalla formattazione delle tabelle (Styler.format) — unica fonte.
IT_NUM_FORMAT = dict(precision=2, thousands=".", decimal=",")


def clean_code(text: str) -> str:
    """Rimuove i fence markdown (```python ... ```) che i modelli spesso aggiungono."""
    cleaned = re.sub(r'```(?:python)?\s*|\s*```', '', text or '')
    return cleaned.strip()


# Oltre 2^53 un float non rappresenta più esattamente gli interi: le cifre in
# fondo non sono un dato, sono la codifica binaria che affiora.
_OLTRE_LA_PRECISIONE = 2 ** 53
# Sotto questa soglia "0,00" nasconderebbe un valore che c'è: servono più decimali.
_SOTTO_LA_SOGLIA = 0.005
# Sotto QUESTA nemmeno quattro decimali bastano, e si passa alla potenza di dieci.
_MINUSCOLO = 0.0001


def fmt_num(x) -> str:
    """
    Formatta un numero in modo leggibile all'italiana: separatore delle migliaia '.'
    e decimale ','. Interi e valori grandi senza decimali (2.261.537), valori piccoli
    con due decimali (230,77). Non-numeri restituiti invariati.

    Ai due estremi si passa alla notazione scientifica (`1,00e+308`, `1,00e-10`):
    là le cifre della forma italiana o non sono un dato o non ci sono affatto.
    """
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if x != x:  # NaN
        return "—"
    # L'infinito va intercettato PRIMA di `int(x)`, che su di esso solleva
    # OverflowError. Non è un caso di scuola: un CSV con `inf` fra i valori
    # faceva rispondere **500** all'intero report della demo — il difetto si
    # propagava da qui, perché ogni numero mostrato dall'app passa di qua.
    # Si mostra il simbolo invece di "—": un valore infinito non è un valore
    # mancante, e confonderli nasconderebbe un dato anomalo da correggere.
    if x in (float("inf"), float("-inf")):
        return "∞" if x > 0 else "−∞"
    # Fuori scala si passa alla notazione scientifica. Il confine non è estetico:
    # oltre 2^53 un float non rappresenta più gli interi esatti, quindi le cifre
    # che si stamperebbero sono un artefatto della codifica binaria e non un dato
    # — `1e308` diventava una riga da 309 cifre, che nella struttura delle
    # colonne sfondava la tabella e in una card KPI il riquadro. Sotto lo zero
    # virgola qualcosa vale il difetto opposto: `1e-10` si stampava "0,00", cioè
    # zero, che è un numero diverso.
    if abs(x) >= _OLTRE_LA_PRECISIONE or 0 < abs(x) < _MINUSCOLO:
        return f"{x:.2e}".replace(".", ",")
    # Piccolo ma non minuscolo: bastano più decimali, e si leggono meglio di una
    # potenza di dieci in un'app che parla di sconti e percentuali.
    if 0 < abs(x) < _SOTTO_LA_SOGLIA:
        return f"{x:,.4f}".replace(",", "§").replace(".", ",").replace("§", ".")
    if x == int(x) or abs(x) >= 1000:
        s = f"{x:,.0f}"
    else:
        s = f"{x:,.2f}"
    # da formato US (1,234.56) a IT (1.234,56)
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def with_unit(text: str, unit: str) -> str:
    """
    Antepone l'unità di misura a un testo destinato all'LLM.

    Il modello non ha modo di conoscerla: senza questo aggancio inventerebbe
    un'unità plausibile ('euro', 'unità') o la ometterebbe, e i numeri della
    narrazione perderebbero significato.
    """
    return f"L'unità di misura dei valori è '{unit}'.\n{text}" if unit else text


def column_kind(series: pd.Series) -> str:
    """Classifica il tipo di una colonna in una categoria comprensibile."""
    if pd.api.types.is_bool_dtype(series):
        return "booleana"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "data"
    if pd.api.types.is_numeric_dtype(series):
        return "numerica"
    return "testo"
