"""
Risposte a domande SUL PROGETTO, fondate sulla sua documentazione.

A cosa serve: chi valuta questo lavoro — un recruiter, un tech lead — ha domande
sul progetto, non sui dati di esempio ("come funziona la sandbox?", "perché
Streamlit?"). Farle a voce richiede che io sia presente; leggere 14.000 parole di
documentazione richiede tempo che nessuno ha. Qui le si può fare all'app.

## La regola che governa il modulo

È lo stesso principio del resto dell'applicazione, applicato a un'altra materia:
**il modello non aggiunge fatti**. Là i numeri li calcola Pandas e l'LLM li
racconta; qui i fatti li contengono i documenti del repository e l'LLM li
riformula. Se la risposta non è nelle fonti recuperate, il prompt gli impone di
dirlo. Un chatbot di portfolio che allucina sul progetto stesso è peggio di
nessun chatbot: sposta il dubbio dal "non lo so" al "non so se ciò che dice è
vero", e quel dubbio contagia tutto il resto.

## Perché recupero LESSICALE e non embedding

La scelta va difesa, perché la moda è l'opposto.

* **Nessuna dipendenza in più.** Gli embedding richiedono o un modello locale
  (centinaia di MB, che su Streamlit Community Cloud non stanno) o una chiamata
  API per ogni domanda *e* per ogni riscrittura dell'indice.
* **Il corpus gioca a favore del lessicale.** Non sono testi generici dove
  servono i sinonimi: sono documenti tecnici pieni di termini propri e rari —
  `_SafeModule`, allowlist, `retryable`, pickle, `cqi`. Chi chiede della sandbox
  usa quelle parole, e l'IDF le premia esattamente perché compaiono in poche
  sezioni.
* **È deterministico, quindi testabile.** `tests/test_project_qa.py` verifica che
  una domanda nota recuperi la sezione giusta. Con gli embedding si potrebbe
  testare solo che "qualcosa" torni.

Il limite, dichiarato: una domanda che non condivide **nessuna** parola con la
documentazione recupera male. Mitigazione parziale con un piccolo dizionario di
sinonimi (`_SINONIMI`) per i termini dove il divario è prevedibile
("velocità"→"latenza", "costo"→"pricing").

## Confini

Funzioni pure: nessuno Streamlit, nessuna chiamata di rete in questo modulo.
`answer()` riceve un `LLMProvider` già costruito — così i test coprono il
recupero e la costruzione del prompt senza toccare la rete.
"""
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from nlda import prompts
from nlda.log import get_logger
from nlda.sanitize import sanitize

log = get_logger(__name__)

# Radice del repository: due livelli sopra questo file (nlda/ -> radice).
_RADICE = Path(__file__).resolve().parent.parent

# I documenti che il chatbot può citare, in ordine di specificità. Sono TUTTI già
# nel repository pubblico: la base di conoscenza non è una copia da tenere in
# sincrono a mano, sono i file che un lettore troverebbe da sé.
FONTI: tuple[tuple[str, str], ...] = (
    ("docs/DOCUMENTAZIONE_TECNICA.md", "Documentazione tecnica"),
    ("ARCHITECTURE.md", "Architettura e decisioni"),
    ("THREAT_MODEL.md", "Modello di minaccia"),
    ("VALUE.md", "Valore del progetto"),
    ("README.md", "README"),
    ("DEPLOY.md", "Deploy"),
)

# Parole troppo comuni per discriminare: senza rimuoverle, "come funziona la
# sandbox" recupererebbe le sezioni con più occorrenze di "come" e "la".
_STOPWORD = {
    "a", "ad", "ai", "al", "alla", "alle", "allo", "anche", "che", "chi", "ci",
    "co", "col", "come", "con", "cosa", "così", "cui", "da", "dai", "dal", "dalla",
    "degli", "dei", "del", "della", "delle", "dello", "di", "dove", "e", "ed", "è",
    "essere", "fa", "fare", "gli", "ha", "hai", "hanno", "ho", "i", "il", "in",
    "invece", "io", "la", "le", "lo", "ma", "mi", "ne", "nel", "nella", "nelle",
    "no", "noi", "non", "o", "per", "perche", "perché", "però", "più", "può",
    "quale", "quali", "quando", "quanto", "quest", "questa", "queste", "questi",
    "questo", "qui", "se", "sei", "sono", "su", "sua", "sue", "sui", "sul",
    "sulla", "suo", "ti", "tra", "tu", "tuo", "un", "una", "uno", "va", "vi",
    "viene", "sì", "si", "solo", "già", "molto", "poi", "quindi", "the",
    # Interrogativi: senza di loro "qual è la ricetta della carbonara?" pescava le
    # tre sezioni che contengono "qual" e le presentava come pertinenti. Una parola
    # rara ma priva di contenuto è il peggior caso per l'IDF, che la premia proprio
    # perché è rara.
    "qual", "quant", "quanta", "quante", "quanti", "cos", "sarebbe", "vorrei",
    "dimmi", "spiegami", "parlami", "vuol", "dire",
    # "come FUNZIONA la sandbox" è l'apertura di domanda più comune, e il verbo non
    # discrimina nulla. Va tolto qui, PRIMA della radice: `_tokenizza` filtra
    # sulla parola intera, così "funziona" sparisce ma il sostantivo "funzione"
    # resta. Senza questa distinzione le due cadevano sulla stessa radice e la
    # domanda sulla sandbox pescava la sezione intitolata "funzione per funzione".
    "funziona", "funzionano", "funzionare", "funziono",
}

# Titoli che non sono una fonte: un indice risponde "dove guardare", non "cosa".
_TITOLI_NON_CITABILI = {"indice", "sommario", "table of contents"}

# Suffissi flessivi italiani, dal più lungo al più corto: si toglie il primo che
# combacia. Serve perché la domanda e la documentazione usano forme diverse della
# stessa parola — "come TESTATE" contro "i TEST", "i GRAFICI" contro "il GRAFICO" —
# e un confronto tra parole intere le tratterebbe come termini estranei.
_SUFFISSI = ("azione", "azioni", "amento", "amenti", "mente", "ando", "endo",
             "zione", "zioni", "ato", "ata", "ati", "ate", "ito", "ita", "iti",
             "ite", "are", "ere", "ire", "ano", "ono", "aggio")
_MIN_RADICE = 4


def _radice(parola: str) -> str:
    """
    Normalizzazione morfologica volutamente GROSSOLANA.

    Non è uno stemmer linguistico: taglia i suffissi più comuni e la vocale
    finale, fermandosi a quattro caratteri. Basta perché la stessa funzione si
    applica alla domanda E al corpus: non serve che la radice sia corretta in
    italiano, serve che due forme della stessa parola cadano sulla stessa chiave.
    Un termine tecnico ("sandbox", "pickle", "allowlist") non ha questi suffissi e
    resta intatto.
    """
    for s in _SUFFISSI:
        if parola.endswith(s) and len(parola) - len(s) >= _MIN_RADICE:
            return parola[: -len(s)]
    if len(parola) > _MIN_RADICE and parola[-1] in "aeio":
        return parola[:-1]
    # Plurale inglese: la documentazione alterna "provider" e "providers", "chunk"
    # e "chunks". La soglia più alta evita di intaccare parole che finiscono in -s
    # per natura ("os", "css").
    if len(parola) > 5 and parola.endswith("s"):
        return parola[:-1]
    return parola

# Divari di vocabolario prevedibili tra come si chiede e come è scritto.
_SINONIMI = {
    "velocita": ["latenza", "prestazioni", "performance", "lento", "veloce"],
    "velocità": ["latenza", "prestazioni", "performance"],
    "costo": ["pricing", "token", "spesa", "usd"],
    "costa": ["pricing", "token", "spesa"],
    "soldi": ["pricing", "costo", "spesa"],
    "sicurezza": ["sandbox", "validator", "allowlist", "injection"],
    "sicuro": ["sandbox", "allowlist", "fail-closed"],
    "attacco": ["injection", "escape", "minaccia", "ostile"],
    "hacker": ["injection", "escape", "minaccia"],
    "grafici": ["plotly", "charts", "figura", "istogramma"],
    "grafico": ["plotly", "charts", "figura"],
    "database": ["dataset", "dataframe", "pandas", "csv"],
    "ia": ["llm", "modello", "provider"],
    "ai": ["llm", "modello", "provider"],
    "intelligenza": ["llm", "modello"],
    "errori": ["failure", "retryable", "guasto", "eccezione"],
    "errore": ["failure", "retryable", "guasto"],
    "test": ["pytest", "copertura", "property"],
    "deploy": ["streamlit", "container", "docker", "secrets"],
    "scalabilita": ["righe", "memoria", "max_rows", "grandi"],
    "scalabilità": ["righe", "memoria", "max_rows", "grandi"],
}

_PAROLA = re.compile(r"[a-zà-öø-ÿ0-9_]+", re.I)

# Si spezza sui titoli (### e ##), che nella documentazione corrispondono già a un
# argomento. Sotto questa soglia una "sezione" è un residuo di impaginazione (una
# riga di rimando, un separatore) e non una fonte: la si scarta invece di fonderla
# con un'altra, per non attribuire testo al titolo sbagliato.
_MIN_PAROLE_SEZIONE = 8
MAX_FRAMMENTI = 5            # quante sezioni entrano nel prompt
MAX_PAROLE_CONTESTO = 2600   # tetto complessivo del contesto passato al modello


@dataclass(frozen=True)
class Frammento:
    """Una sezione della documentazione, con la sua provenienza citabile."""

    fonte: str        # etichetta leggibile del documento
    titolo: str       # percorso dei titoli, es. "8. La sandbox > 8.3 Allowlist"
    testo: str

    @property
    def citazione(self) -> str:
        return f"{self.fonte} — {self.titolo}" if self.titolo else self.fonte


def _tokenizza(testo: str) -> list[str]:
    """Parole significative, ridotte alla loro radice grossolana."""
    return [_radice(p) for p in (m.group().lower() for m in _PAROLA.finditer(testo))
            if p not in _STOPWORD and len(p) > 1]


# I sinonimi sopra sono scritti in forma piena, perché così si leggono; il confronto
# però avviene fra RADICI. Si normalizzano una volta qui: scriverli già stemmati
# renderebbe la tabella illeggibile ("velocit" → "latenz"), e tenere le due forme
# a mano è il tipo di doppione che si desincronizza al primo termine aggiunto.
_SINONIMI_RADICE: dict[str, list[str]] = {}
for _k, _v in _SINONIMI.items():
    _SINONIMI_RADICE.setdefault(_radice(_k), []).extend(_radice(x) for x in _v)


def _espandi(termini: list[str]) -> list[str]:
    """Aggiunge i sinonimi noti (già ridotti a radice), senza toccare gli originali."""
    fuori = list(termini)
    for t in termini:
        fuori.extend(_SINONIMI_RADICE.get(t, ()))
    return fuori


def _spezza_documento(testo: str, fonte: str) -> list[Frammento]:
    """
    Divide un documento in frammenti sui titoli markdown, conservando il percorso
    dei titoli (serve a citare la provenienza in modo utile a chi legge).

    I blocchi di codice vengono attraversati senza interpretarne il contenuto: un
    '#' di commento Python non è un titolo, e senza questa attenzione un
    diagramma spezzerebbe la sezione a metà.
    """
    frammenti: list[Frammento] = []
    h2 = h3 = ""
    corrente: list[str] = []
    in_codice = False

    def chiudi() -> None:
        corpo = "\n".join(corrente).strip()
        titolo = " > ".join(x for x in (h2, h3) if x)
        if (h3 or h2).strip().lower() in _TITOLI_NON_CITABILI:
            return
        # Una sezione con poche parole resta comunque un frammento PROPRIO.
        # Fondere le sezioni corte in quella precedente — come si faceva prima —
        # sembrava un'economia, e invece attribuiva il testo al titolo sbagliato:
        # in un assistente che cita le fonti, una citazione errata è peggio di una
        # citazione breve. Il costo di un frammento corto è qualche token; il costo
        # di una fonte sbagliata è la fiducia in tutte le altre.
        if len(corpo.split()) >= _MIN_PAROLE_SEZIONE:
            frammenti.append(Frammento(fonte, titolo, corpo))

    for riga in testo.split("\n"):
        if riga.lstrip().startswith("```"):
            in_codice = not in_codice
            corrente.append(riga)
            continue
        if not in_codice:
            m = re.match(r"^(#{2,3})\s+(.*)$", riga)
            if m:
                chiudi()
                corrente = []
                if len(m.group(1)) == 2:
                    h2, h3 = m.group(2).strip(), ""
                else:
                    h3 = m.group(2).strip()
                continue
        corrente.append(riga)

    chiudi()
    return frammenti


@lru_cache(maxsize=1)
def base_di_conoscenza() -> tuple[Frammento, ...]:
    """
    Tutti i frammenti citabili, letti una volta sola.

    In cache perché Streamlit riesegue lo script a ogni interazione e rileggere
    sei file a ogni click sarebbe uno spreco. `lru_cache` e non `st.cache_data`:
    questo modulo non deve conoscere Streamlit.
    """
    fuori: list[Frammento] = []
    for percorso, etichetta in FONTI:
        f = _RADICE / percorso
        if not f.exists():
            # Una fonte assente non è un errore fatale: il chatbot risponde con
            # quello che ha. Lo si annota, perché in deploy è un segnale utile.
            log.warning("qa_fonte_assente", extra={"percorso": percorso})
            continue
        fuori.extend(_spezza_documento(f.read_text(encoding="utf-8"), etichetta))
    log.info("qa_base_pronta", extra={"frammenti": len(fuori)})
    return tuple(fuori)


@lru_cache(maxsize=4)
def _indice(frammenti: tuple[Frammento, ...]) -> dict[str, list[tuple[int, float]]]:
    """
    Indice invertito: termine -> [(frammento, peso già calcolato), ...].

    Il punteggio di un frammento per un termine non dipende dalla domanda — è
    `idf * (1 + log tf) * peso_titolo / sqrt(lunghezza)`, tutta roba nota dal
    corpus. Calcolarlo a ogni ricerca significava ritokenizzare 19.000 parole per
    rispondere a una domanda di tre: **42 ms, di cui 33 di sola tokenizzazione**,
    e quasi tre milioni di `str.endswith` ogni dieci ricerche (`_radice` prova 21
    suffissi per token).

    Precalcolato una volta: **0,02 ms** per ricerca, risultati identici. Il
    confronto sta in `tests/test_project_qa.py`.

    La cache è sui frammenti, non globale, così i test possono passare un corpus
    finto senza inquinare quello vero. `Frammento` è congelato e la base arriva
    come tupla, quindi è lecito usarla come chiave.
    """
    corpi = [_tokenizza(f.titolo + " " + f.testo) for f in frammenti]
    n = len(frammenti)

    presenze: dict[str, int] = {}
    for corpo in corpi:
        for t in set(corpo):
            presenze[t] = presenze.get(t, 0) + 1

    fuori: dict[str, list[tuple[int, float]]] = {}
    for i, corpo in enumerate(corpi):
        if not corpo:
            continue
        conta: dict[str, int] = {}
        for t in corpo:
            conta[t] = conta.get(t, 0) + 1
        titolo = set(_tokenizza(frammenti[i].titolo))
        norma = math.sqrt(len(corpo))
        for t, tf in conta.items():
            idf = math.log(1 + n / (1 + presenze.get(t, 0)))
            peso = 1.0 + (0.6 if t in titolo else 0.0)   # nel titolo conta di più
            fuori.setdefault(t, []).append((i, idf * (1 + math.log(tf)) * peso / norma))
    return fuori


def cerca(domanda: str, k: int = MAX_FRAMMENTI,
          base: "tuple[Frammento, ...] | None" = None) -> list[Frammento]:
    """
    I `k` frammenti più pertinenti alla domanda, dal più rilevante.

    Punteggio in stile TF-IDF: ogni termine della domanda pesa per il proprio IDF
    (raro = discriminante) per la frequenza nel frammento, smorzata dal logaritmo
    perché la decima occorrenza di "sandbox" non conta come la prima. Si
    normalizza sulla radice della lunghezza: senza, le sezioni lunghe vincerebbero
    sempre, a prescindere dalla pertinenza.
    """
    frammenti = base if base is not None else base_di_conoscenza()
    if not frammenti:
        return []

    termini = _espandi(_tokenizza(domanda))
    if not termini:
        return []

    indice = _indice(frammenti)
    punteggi: list[tuple[float, int]] = []
    parziali: dict[int, float] = {}
    for t in termini:
        for i, peso in indice.get(t, ()):
            parziali[i] = parziali.get(i, 0.0) + peso
    punteggi = [(p, i) for i, p in parziali.items() if p > 0]

    punteggi.sort(reverse=True)
    return [frammenti[i] for _, i in punteggi[:k]]


def costruisci_contesto(frammenti: list[Frammento]) -> str:
    """
    Impagina i frammenti per il prompt, con un'etichetta di provenienza su ognuno.

    L'etichetta non è decorativa: è ciò che permette al modello di citare la
    fonte, e a chi legge di andare a verificare. Il tetto di parole evita che una
    domanda generica, che recupera cinque sezioni lunghe, faccia esplodere il
    costo del turno.
    """
    pezzi: list[str] = []
    totale = 0
    for f in frammenti:
        parole = f.testo.split()
        if totale + len(parole) > MAX_PAROLE_CONTESTO:
            parole = parole[: max(0, MAX_PAROLE_CONTESTO - totale)]
            if not parole:
                break
        totale += len(parole)
        pezzi.append(f"### FONTE: {f.citazione}\n{' '.join(parole)}")
    return "\n\n".join(pezzi)


def answer(provider, domanda: str, *, k: int = MAX_FRAMMENTI) -> tuple[str, list[Frammento]]:
    """
    Risponde a una domanda sul progetto e restituisce anche le fonti usate.

    La domanda è sanitizzata come qualunque testo non fidato: arriva da un campo
    pubblico e finisce in un prompt (vedi `nlda.sanitize`). Se il recupero non
    trova nulla non si chiama affatto il modello: sarebbe una spesa per farsi
    dire "non lo so" da un contesto vuoto.
    """
    pulita = sanitize(domanda, max_len=300)
    if not pulita:
        return ("Fai una domanda sul progetto: architettura, sicurezza della sandbox, "
                "scelte tecniche, test, deploy.", [])

    trovati = cerca(pulita, k=k)
    if not trovati:
        log.info("qa_nessun_frammento", extra={"lunghezza_domanda": len(pulita)})
        return ("Non trovo nulla nella documentazione del progetto su questo. "
                "Prova a riformulare usando i termini del progetto (sandbox, provider, "
                "loader, KPI, deploy), oppure guarda il README nel repository.", [])

    system = prompts.load("project_qa")
    user = f"DOCUMENTAZIONE DISPONIBILE\n\n{costruisci_contesto(trovati)}\n\nDOMANDA\n{pulita}"
    testo = provider.generate(system, user)
    log.info("qa_risposta", extra={"frammenti_usati": len(trovati),
                                  "fonti": [f.citazione for f in trovati]})
    return testo.strip(), trovati
