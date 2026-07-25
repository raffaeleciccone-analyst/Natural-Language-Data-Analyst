"""
Controlli deterministici su una risposta, per dare all'utente modo di FIDARSI (o
di dubitare) senza rileggere il codice.

Il punto debole di un'app che genera codice con un LLM è la risposta "eseguibile ma
sbagliata": Pandas non solleva, ma il numero è quello di una domanda diversa. Qui
non si pretende di rilevare ogni errore — impossibile senza conoscere l'intento —
ma di offrire due appigli concreti:

* `columns_referenced`: quali colonne del dataset il codice ha davvero toccato,
  così l'utente vede su cosa poggia la risposta;
* `sanity_warnings`: pochi segnali ad ALTA confidenza che qualcosa non torna (una
  quota fuori da 0–100%, un risultato NaN, una tabella vuota). Conservativi di
  proposito: un avviso che grida al lupo troppo spesso viene ignorato.

Funzioni pure, nessuno Streamlit: la UI le chiama, ma si testano da sole.
"""
import ast
import math

import pandas as pd


def columns_referenced(code: str, columns) -> list[str]:
    """
    Colonne del dataset citate nel codice generato: i literal di stringa che
    combaciano con un nome di colonna reale. Non riporta nulla che non sia una
    colonna, quindi niente falsi positivi; può mancare un accesso per attributo
    (`df.Sales`), ma il prompt insegna la forma `df['Sales']`.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    nomi = {str(c) for c in columns}
    # ast.walk è in ampiezza, non in ordine sorgente: si riordina per posizione, così
    # "df.groupby('Region')['Sales']" dà [Region, Sales] come si legge nel codice.
    trovate = sorted(
        ((n.value, n.lineno, n.col_offset) for n in ast.walk(tree)
         if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in nomi),
        key=lambda t: (t[1], t[2]),
    )
    return list(dict.fromkeys(valore for valore, _, _ in trovate))  # dedup, ordine di comparsa


def _string_keys(slice_node) -> list[str]:
    """Le chiavi-stringa di un subscript: `df['A']` → ['A']; `df[['A','B']]` →
    ['A','B']. Maschere booleane, slice e chiavi calcolate non danno stringhe."""
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return [slice_node.value]
    if isinstance(slice_node, (ast.List, ast.Tuple)):
        return [el.value for el in slice_node.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)]
    return []


def unknown_columns_referenced(code: str, columns) -> list[str]:
    """
    Colonne LETTE dal dataframe originale (`df['X']`) che NON esistono nel dataset.

    Coglie il caso in cui il codice generato inventa una colonna: è la stessa causa
    di un KeyError a runtime, ma vista PRIMA di eseguire, così l'app non calcola su
    una colonna fantasma. Volutamente conservativa per non avere falsi positivi:
    - guarda SOLO i subscript sul nome `df`; i frame DERIVATI hanno colonne nuove e
      legittime che qui non conosciamo (`detail['percentuale']` non è toccato);
    - solo in LETTURA: `df['nuova'] = ...` CREA una colonna, non la inventa;
    - solo chiavi che sono stringhe letterali (`df[col]` con variabile è ignorato).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    nomi = {str(c) for c in columns}
    ignote: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == "df"
                and isinstance(node.ctx, ast.Load)):
            continue
        for chiave in _string_keys(node.slice):
            if chiave not in nomi and chiave not in ignote:
                ignote.append(chiave)
    return ignote


def sanity_warnings(value) -> list[str]:
    """
    Segnali deterministici che un risultato è sospetto. Volutamente pochi e ad alta
    confidenza: meglio tacere che allarmare su una risposta corretta.
    """
    avvisi: list[str] = []
    if isinstance(value, pd.DataFrame):
        if value.empty:
            avvisi.append("Il risultato è una tabella vuota: nessun dato corrisponde.")
        # 'percentuale' è la quota sul totale che il prompt insegna a produrre: una
        # quota fuori da 0–100% non è un'opinione, è un errore di calcolo.
        if "percentuale" in value.columns:
            perc = pd.to_numeric(value["percentuale"], errors="coerce")
            if bool(((perc < -0.01) | (perc > 100.01)).any()):
                avvisi.append("Una percentuale è fuori dall'intervallo 0–100%: "
                              "il calcolo potrebbe essere sbagliato.")
    elif isinstance(value, pd.Series):
        if value.empty:
            avvisi.append("Il risultato è una serie vuota: nessun dato corrisponde.")
    elif isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            avvisi.append("Il risultato è NaN o infinito: probabile divisione per zero "
                          "o colonna non numerica.")
    return avvisi
