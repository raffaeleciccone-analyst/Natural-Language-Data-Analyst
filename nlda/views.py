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


def join_warning(left: pd.DataFrame, right: pd.DataFrame, merged: pd.DataFrame,
                 left_on: str, right_on: str) -> str | None:
    """
    Avvisa quando l'unione ha MOLTIPLICATO le righe invece di affiancare colonne.

    È il difetto più insidioso del join, perché non fallisce: se la chiave si
    ripete in uno dei due file, ogni riga dell'altro si duplica per ogni
    corrispondenza. Il totale delle vendite raddoppia, e nulla lo segnala — un
    numero sbagliato con l'aria di essere giusto.

    Il sintomo (più righe di entrambi i file) e la causa (la chiave non è unica)
    si controllano entrambi, e l'avviso dice quale delle due chiavi è duplicata:
    è l'informazione che serve per rimediare, cioè aggregare quel file sulla
    chiave prima di unirlo.

    `None` quando non c'è nulla da dire — il caso normale.
    """
    # Il metro è il PRIMO file, non il più grande dei due: unire serve ad
    # aggiungere colonne alle sue righe, quindi ritrovarsene di più significa che
    # sono state ripetute. Confrontare con `max(len(left), len(right))` sembrava
    # equivalente e non lo è: 2 righe unite a 3 ne producono 3, e la
    # moltiplicazione — un ordine contato due volte — passava inosservata.
    if len(merged) <= len(left):
        return None
    # La causa sta sempre a destra: le chiavi ripetute nel PRIMO file non
    # moltiplicano nulla, perché ognuna trova le stesse corrispondenze.
    if right_on not in right.columns or not bool(right[right_on].duplicated().any()):
        return None

    return (f"L'unione ha prodotto {len(merged)} righe dalle {len(left)} del primo file: "
            f"la chiave '{right_on}' si ripete nel secondo, quindi alcune righe sono state "
            "duplicate. Se non è voluto, i totali risulteranno gonfiati: aggrega il secondo "
            "file sulla chiave prima di unirlo.")
