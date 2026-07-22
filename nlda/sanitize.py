"""
Sanitizzazione dei dati non fidati prima che raggiungano il prompt o il Markdown.

Tutto ciò che proviene dal file caricato è dato ostile per definizione: nomi di
colonna e valori di cella finiscono nel testo che si manda al modello, e un
testo che finisce in un prompt può contenere istruzioni. È il vettore di prompt
injection più diretto che questa applicazione abbia.

Perché una funzione sola: prima ce n'erano due quasi identiche, in `agent.py` e
in `loader.py`. Due copie della stessa difesa significano che un rafforzamento
ne raggiunge una e dimentica l'altra — ed è esattamente ciò che era successo.

Cosa rimuove, e perché ogni categoria conta:

* **caratteri di controllo** (`\\x00-\\x1f`, `\\x7f`): spezzano la struttura del
  prompt e possono terminare anticipatamente un valore;
* **spaziature verticali** (a capo, tab): un valore che contiene un a capo può
  simulare una nuova riga di istruzioni nell'elenco che descrive lo schema;
* **unicode invisibile** (zero-width, BOM, separatori di riga unicode): non si
  vedono rileggendo il file ma il modello li legge, e permettono di nascondere
  testo dentro un valore apparentemente innocuo;
* **override bidirezionali** (`\\u202a-\\u202e`, `\\u2066-\\u2069`): riordinano
  visivamente il testo, così ciò che una persona legge e ciò che il modello
  riceve possono essere due cose diverse;
* **backtick**: chiudono e riaprono i blocchi di codice;
* **metacaratteri Markdown di link** (opzionali): un valore mostrato in una card
  potrebbe altrimenti iniettare un link cliccabile nella pagina.
"""
import re

# Controllo, tranne tab/CR/LF che diventano spazi invece di sparire (togliere un
# a capo senza sostituirlo unirebbe due parole).
_DA_RIMUOVERE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"   # caratteri di controllo
    "​-‏"                      # zero-width e marcatori di direzione
    "  "                       # separatori di riga/paragrafo unicode
    "‪-‮"                      # override bidirezionale
    "⁦-⁩"                      # isolamenti bidirezionali
    "﻿"                             # BOM / zero-width no-break space
    "]"
)
_SPAZI_VERTICALI = re.compile(r"[\t\n\r]+")

MAX_LEN_VALORE = 40   # un campione di cella: serve a dare il tipo, non il contenuto
MAX_LEN_NOME = 60     # un nome di colonna va riconosciuto dal modello, quindi più lungo


def sanitize(value, max_len: int = MAX_LEN_VALORE, *, strip_markdown: bool = False) -> str:
    """
    Rende un valore non fidato sicuro da inserire in un prompt o in del Markdown.

    Tronca a `max_len`: oltre a contenere i token, limita quanto testo un file
    ostile può iniettare in una singola cella. Con `strip_markdown` rimuove anche
    i metacaratteri dei link, per i valori che finiscono nell'interfaccia.
    """
    s = _DA_RIMUOVERE.sub("", str(value))
    s = _SPAZI_VERTICALI.sub(" ", s)
    s = s.replace("`", "'")
    if strip_markdown:
        s = s.translate({ord(c): None for c in "[]()"})
    s = s.strip()
    return s[:max_len] + "…" if len(s) > max_len else s
