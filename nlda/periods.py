"""
Confronto di una misura tra periodi consecutivi (mese / trimestre / anno).

Motore deterministico condiviso da due strade: la sandbox lo espone al codice
generato (`compare_periods(df, ...)`), e la UI lo chiama direttamente per la
sezione "Confronto tra periodi". Un'unica implementazione, testata: il modello non
deve reinventare il calcolo della variazione percentuale a ogni domanda, e la UI
non duplica la logica.
"""
import pandas as pd

from nlda.utils import to_datetime_quiet

# Etichette accettate → alias di periodo di pandas (to_period). Si accettano sia i
# nomi italiani (che usa la UI e che il modello legge nel prompt) sia i codici brevi.
_FREQ = {"mese": "M", "trimestre": "Q", "anno": "Y", "m": "M", "q": "Q", "y": "Y"}


def compare_periods(df: pd.DataFrame, date_col: str, measure: str,
                    freq: str = "trimestre", agg: str = "sum") -> pd.DataFrame:
    """
    Confronta `measure` tra periodi consecutivi ricavati da `date_col`.

    Ritorna un DataFrame ordinato nel tempo con tre colonne: `periodo` (etichetta
    leggibile, es. '2023Q1'), la misura aggregata, e `variazione_%` rispetto al
    periodo precedente (NaN sul primo, che non ha un prima). `freq` è mese,
    trimestre o anno; `agg` è l'aggregazione (sum, mean, ...).
    """
    code = _FREQ.get(str(freq).strip().lower())
    if code is None:
        raise ValueError(f"Frequenza non valida: {freq!r}. Usa 'mese', 'trimestre' o 'anno'.")

    dates = to_datetime_quiet(df[date_col])
    mask = dates.notna()
    if not mask.any():
        raise ValueError(f"La colonna '{date_col}' non contiene date riconoscibili.")

    periods = dates[mask].dt.to_period(code)
    grouped = df.loc[mask, measure].groupby(periods).agg(agg).sort_index()

    out = grouped.reset_index()
    out.columns = ["periodo", measure]
    out["periodo"] = out["periodo"].astype(str)
    out["variazione_%"] = (grouped.pct_change() * 100).round(1).to_numpy()
    return out
