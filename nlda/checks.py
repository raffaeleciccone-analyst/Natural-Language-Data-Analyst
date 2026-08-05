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
  proposito: un avviso che grida al lupo troppo spesso viene ignorato;
* `declared_mapping` / `mapping_warnings`: la mappa termine→colonna che il modello
  è tenuto a dichiarare, verificata contro le colonne vere. Attacca il caso
  peggiore — la grandezza chiesta non esiste e il modello ne usa una simile senza
  dirlo — che nessun controllo lessicale può cogliere, perché richiede di sapere
  se 'vendite'→'Sales' è una traduzione o una sostituzione.

`question_warnings` è l'unica porta da cui entrambe le interfacce prendono gli
avvisi sulla domanda: due porte diventano due comportamenti diversi.

Funzioni pure, nessuno Streamlit: la UI le chiama, ma si testano da sole.
"""
import ast
import math
import re

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


# Una colonna 'nominata' dalla domanda: `df['X']`, oppure 'colonna/campo/column'
# seguito da virgolette o da un Nome Capitalizzato (anche composto, es. 'Order Date').
# Il vincolo sull'iniziale maiuscola è ciò che dà PRECISIONE: assorbe i nomi
# multi-parola reali e tiene fuori le parole-funzione ('la colonna con piu' vendite'
# non cattura 'con'). Solo il keyword è case-insensitive.
_COLONNA_NOMINATA = re.compile(
    r"df\s*\[\s*['\"](?P<sub>[^'\"]+)['\"]\s*\]"
    r"|(?i:colonn[ae]|campo|column)\s+"
    r"(?:['\"](?P<quot>[^'\"]+)['\"]"
    r"|(?P<cap>[A-Z][\w-]*(?:\s+[A-Z][\w-]*)*))"
)


def claimed_missing_columns(question: str, columns) -> list[str]:
    """
    Nomi che la DOMANDA presenta esplicitamente come colonne (`df['X']`, 'colonna X',
    'campo X') ma che NON esistono nel dataset.

    È il segnale deterministico dietro l'avviso anti-allucinazione: quando l'utente
    nomina una colonna inventata, il modello tende a sostituirla in silenzio con una
    reale (es. Sales) e la risposta la spaccia per quella chiesta. Qui NON si indovina
    l'intento — si cattura solo ciò che l'utente ha marcato come colonna — così
    l'avviso resta ad alta precisione: niente falsi positivi su valori o concetti
    generici, e i nomi di colonna composti (`Order Date`) non vengono spezzati.
    """
    nomi = {str(c) for c in columns}
    fuori: list[str] = []
    for m in _COLONNA_NOMINATA.finditer(question or ""):
        nome = (m.group("sub") or m.group("quot") or m.group("cap") or "").strip()
        if nome and nome not in nomi and nome not in fuori:
            fuori.append(nome)
    return fuori


# La riga con cui il modello dichiara su quale colonna sta rispondendo:
#     # mappa: profitto -> Profit
# La parola chiave è insensibile alle maiuscole e tollera lo spazio ballerino,
# perché è un modello a scriverla e la regolarità non gliela si può imporre.
_RIGA_MAPPA = re.compile(
    r"^\s*#\s*mappa\s*:\s*(?P<termine>[^\n>]+?)\s*->\s*(?P<colonna>[^\n]+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Come il modello dichiara "per questa grandezza non c'è nessuna colonna".
_NESSUNA = "nessuna"


def declared_mapping(code: str) -> dict[str, str]:
    """
    La mappa termine→colonna che il modello ha dichiarato nel codice.

    È il perno del controllo sulla sostituzione semantica. Il difetto che chiude:
    l'utente chiede il "profitto", il dataset non ce l'ha, e il modello risponde
    con le vendite senza dirlo — un numero giusto per una domanda che nessuno ha
    fatto. Il controllo lessicale (`claimed_missing_columns`) non lo vede, perché
    'profitto' è un concetto nudo e non una colonna nominata: distinguere
    'concetto-sotto-altro-nome' (vendite→Sales, legittimo) da 'colonna-assente'
    è semantico, e l'unico che ha quel contesto è il modello. Gli si chiede
    quindi di DICHIARARLO, e la dichiarazione la si verifica qui.

    Restare dei COMMENTI è una scelta: non tocca l'esecuzione (l'AST li ignora,
    la sandbox non li vede nemmeno), viaggia già dentro `code` fino a entrambe le
    interfacce senza aggiungere un campo a `Turn`, e l'utente li legge nel
    pannello del codice generato.

    Codice senza dichiarazione → mappa vuota: un modello che non segue la regola
    non deve rompere la risposta, e i corpus registrati prima di questa regola
    continuano a valere.
    """
    mappa: dict[str, str] = {}
    for m in _RIGA_MAPPA.finditer(code or ""):
        termine = m.group("termine").strip().strip("'\"")
        colonna = m.group("colonna").strip().strip("'\"")
        if termine and colonna:
            mappa.setdefault(termine, colonna)
    return mappa


def mapping_warnings(code: str, columns) -> list[str]:
    """
    Gli avvisi che nascono dalla mappa dichiarata. Due casi, entrambi certi —
    nessuno dei due indovina l'intento:

    * il modello dichiara `NESSUNA`: ammette che la grandezza chiesta non ha una
      colonna. Va detto all'utente, perché è esattamente la domanda a cui non si
      può rispondere con questi dati;
    * il modello dichiara una colonna che non esiste: la dichiarazione contraddice
      il dataset. La sandbox lo fermerebbe comunque se poi la leggesse, ma qui il
      motivo si legge nella lingua della domanda ('profitto'), non in quella del
      KeyError.

    NON si segnala il termine mappato su una colonna dal nome diverso: 'vendite'
    → 'Sales' e 'fatturato' → 'Revenue' sono traduzioni corrette, e un avviso su
    ognuna sarebbe rumore su quasi ogni domanda. Gli avvisi qui restano pochi e
    ad alta confidenza, come il resto del modulo.
    """
    nomi = {str(c) for c in columns}
    mappa = declared_mapping(code)
    assenti = [t for t, c in mappa.items() if c.lower() == _NESSUNA]
    inventate = [(t, c) for t, c in mappa.items() if c.lower() != _NESSUNA and c not in nomi]

    avvisi: list[str] = []
    if assenti:
        etichetta = ", ".join(f"«{t}»" for t in assenti)
        verbo = "non ha" if len(assenti) == 1 else "non hanno"
        avvisi.append(f"{etichetta} {verbo} una colonna corrispondente in questo dataset: "
                      "la risposta non può riguardare quella grandezza.")
    for termine, colonna in inventate:
        avvisi.append(f"«{termine}» è stato associato alla colonna '{colonna}', "
                      "che non esiste nel dataset.")
    return avvisi


def question_warnings(question: str, code: str, columns) -> list[str]:
    """
    Tutto ciò che c'è da dire sul rapporto fra la DOMANDA e il codice generato,
    in un elenco solo.

    Esiste per una ragione imparata a spese nostre: l'avviso anti-allucinazione
    era stato scritto dentro la UI di Streamlit, e l'API — quindi la demo React —
    non lo emetteva affatto. La stessa domanda giudicata in due modi a seconda
    dell'interfaccia. Ora la voce è una: chi aggiunge un controllo qui lo aggiunge
    per entrambe, e non può dimenticarne una.
    """
    avviso = hallucination_warning(question, code, columns)
    return ([avviso] if avviso else []) + mapping_warnings(code, columns)


def explanation_is_redundant(question: str, code: str, columns, value) -> bool:
    """
    La narrazione del modello aggiungerebbe qualcosa, o ripeterebbe?

    Nasce da una prova sulla demo pubblica (5 agosto 2026): alla domanda «qual è
    il profitto per regione?» su un dataset che il profitto non ce l'ha, la stessa
    informazione compariva **tre volte** — la frase onesta che il modello mette in
    `result`, il nostro avviso deterministico, e una spiegazione dell'AI che
    riformulava entrambi. Tre modi di dire "quella colonna non c'è", di cui uno
    costa una chiamata al modello e l'attesa che la accompagna.

    Si tace quando ricorrono INSIEME due condizioni:

    * c'è almeno un avviso sul rapporto domanda↔codice (grandezza assente o
      colonna inventata): il turno è già stato giudicato problematico;
    * il risultato è **testo**, cioè già una frase compiuta e non un numero o una
      tabella da commentare.

    Fuori da questo caso non si tocca nulla: la spiegazione di un numero o di una
    tabella è il valore che l'utente viene a cercare, e sopprimerla per prudenza
    sarebbe il difetto opposto — un'app muta che fa risparmiare qualche centesimo.
    """
    return bool(isinstance(value, str) and question_warnings(question, code, columns))


def hallucination_warning(question: str, code: str, columns) -> str | None:
    """
    L'avviso da mostrare quando la DOMANDA nomina una colonna che non esiste.

    Compone il messaggio in un posto solo, perché lo usano DUE interfacce: l'app
    Streamlit e l'API (quindi il frontend React). Prima il testo viveva dentro
    `ui_components`, e l'API non lo produceva affatto: la demo React restava senza
    l'avviso anti-allucinazione anche quando Streamlit lo mostrava — la stessa
    domanda giudicata in due modi a seconda dell'interfaccia. Tenerlo qui, come
    stringa pura senza icone né Markdown, garantisce che le due lo diano identico e
    che ciascuna lo presenti a modo suo.

    Restituisce `None` quando non c'è nulla da segnalare (il caso normale), così chi
    chiama distingue "nessun avviso" da "avviso" senza controllare una lista vuota.
    """
    inventate = claimed_missing_columns(question, columns)
    if not inventate:
        return None
    etichetta = ", ".join(f"«{c}»" for c in inventate)
    verbo = "non è una colonna" if len(inventate) == 1 else "non sono colonne"
    base = columns_referenced(code, columns)
    suffisso = f" La risposta si basa su: {', '.join(base)}." if base else ""
    return f"{etichetta} {verbo} del dataset.{suffisso}"


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
