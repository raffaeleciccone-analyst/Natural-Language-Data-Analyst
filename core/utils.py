"""Utility condivise tra i moduli core (evita duplicazioni)."""
import re

import pandas as pd


def clean_code(text: str) -> str:
    """Rimuove i fence markdown (```python ... ```) che i modelli spesso aggiungono."""
    cleaned = re.sub(r'```(?:python)?\s*|\s*```', '', text or '')
    return cleaned.strip()


def column_kind(series: pd.Series) -> str:
    """Classifica il tipo di una colonna in una categoria comprensibile."""
    if pd.api.types.is_bool_dtype(series):
        return "booleana"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "data"
    if pd.api.types.is_numeric_dtype(series):
        return "numerica"
    return "testo"
