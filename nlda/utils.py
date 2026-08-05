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


def fmt_num(x) -> str:
    """
    Formatta un numero in modo leggibile all'italiana: separatore delle migliaia '.'
    e decimale ','. Interi e valori grandi senza decimali (2.261.537), valori piccoli
    con due decimali (230,77). Non-numeri restituiti invariati.
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
