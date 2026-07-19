"""Utility condivise tra i moduli core (evita duplicazioni)."""
import re

import pandas as pd


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
    if x == int(x) or abs(x) >= 1000:
        s = f"{x:,.0f}"
    else:
        s = f"{x:,.2f}"
    # da formato US (1,234.56) a IT (1.234,56)
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def column_kind(series: pd.Series) -> str:
    """Classifica il tipo di una colonna in una categoria comprensibile."""
    if pd.api.types.is_bool_dtype(series):
        return "booleana"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "data"
    if pd.api.types.is_numeric_dtype(series):
        return "numerica"
    return "testo"
