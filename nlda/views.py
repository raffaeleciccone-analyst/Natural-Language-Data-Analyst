"""
Viste sui dati: filtro e unione.

Due operazioni che RESTRINGONO o ALLARGANO il DataFrame prima che il resto della
pipeline lo veda. Sono preprocessing: il report, la sandbox e i prompt continuano
a lavorare su un solo DataFrame e non sanno che è stato filtrato o unito.

## Perché stanno qui e non in `ui/session.py`

Ci sono nate, insieme al resto del plumbing dell'interfaccia Streamlit. Ma sono
funzioni PURE — pandas e nient'altro — e tenerle in un modulo che importa
Streamlit significava che l'API HTTP, per filtrare un dataset, avrebbe dovuto
importare l'interfaccia grafica di un'altra interfaccia.

È il tipo di dipendenza che non rompe nulla e intanto smentisce l'architettura:
tutto il progetto poggia sull'idea che il dominio non conosca la presentazione.
Spostarle è costato tre import cambiati.
"""
import pandas as pd


def apply_filter(df: pd.DataFrame, spec):
    """
    Applica un filtro `(colonna, valori)` al DataFrame. Ritorna `(df_filtrato,
    etichetta_leggibile)`. `spec` None o vuoto lascia il df invariato.

    Il confronto è su STRINGA, così vale anche per colonne numeriche o miste senza
    sorprese sui tipi: un filtro su un anno funziona che la colonna sia int64,
    object o una mescolanza delle due, come càpita nei file reali.
    """
    if not spec:
        return df, ""
    col, valori = spec
    mask = df[col].astype(str).isin([str(v) for v in valori])
    if len(valori) == 1:
        etichetta = f"{col} = {valori[0]}"
    else:
        etichetta = f"{col} ∈ {{{', '.join(str(v) for v in valori)}}}"
    return df[mask], etichetta


def join_datasets(left: pd.DataFrame, right: pd.DataFrame,
                  left_on: str, right_on: str, how: str = "inner") -> pd.DataFrame:
    """
    Unisce due DataFrame su una coppia di chiavi (merge di pandas). Le colonne del
    secondo file che si chiamano come una del primo prendono il suffisso '_2', così
    nessuna colonna viene sovrascritta in silenzio.

    Il risultato è UN solo DataFrame: il resto dell'app (sandbox, prompt, report)
    non cambia — vede semplicemente più colonne. Il join è un preprocessing, non un
    secondo canale da gestire ovunque.
    """
    return left.merge(right, left_on=left_on, right_on=right_on, how=how,
                      suffixes=("", "_2"))
