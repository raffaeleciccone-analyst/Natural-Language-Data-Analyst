"""
Tipi di ritorno dell'esecuzione del codice generato.

Prima l'esito viaggiava come `dict | str` e il fallimento si riconosceva dal
prefisso "Errore" del messaggio: il control flow dipendeva da una stringa in
italiano, cioè da un testo pensato per l'utente finale. Bastava riformulare un
messaggio per cambiare (silenziosamente) il comportamento dell'app.

Qui l'esito è un tipo: o `ExecutionSuccess` o `ExecutionFailure`. Il chiamante
distingue i due casi sul tipo, e sul fallimento legge `kind` — la CAUSA, non il
testo. Da `kind` discende `retryable`, che chiude un bug reale: un rifiuto della
sandbox non è correggibile da un nuovo tentativo del modello (il codice
rigenerato verrebbe ribloccato dalla stessa regola), quindi non deve innescare
altre chiamate all'LLM.
"""
from dataclasses import dataclass
from typing import Any, Literal, get_args

# Segnaposto per "il codice è andato a buon fine ma non ha prodotto né un valore né
# una figura" (es. un'assegnazione a una variabile che poi non si legge). Non è un
# dato da mostrare: la UI lo salta e l'export lo omette. Vive qui, con gli altri
# tipi d'esito, perché è il runner a produrlo e più moduli lo riconoscono: tenerne
# copie della stringa in giro significa che basta riformularne una perché gli altri
# smettano di riconoscerlo e ricomincino a stamparlo all'utente.
EXECUTED_OK = "Codice eseguito correttamente."

# Causa del fallimento. È la chiave su cui si prendono decisioni (retry, log,
# messaggistica): resta stabile anche se il testo mostrato all'utente cambia.
#   syntax    -> il modello ha prodotto codice non parsificabile (o nessun codice)
#   security  -> la sandbox statica ha rifiutato una costruzione non consentita
#   runtime   -> il codice è valido ma è esploso sui dati (colonna assente, ecc.)
#   timeout   -> esecuzione interrotta perché troppo lenta o troppo pesante
#   provider  -> la comunicazione con il modello è fallita
#   internal  -> l'ambiente di esecuzione isolato non è disponibile o ha risposto male
FailureKind = Literal["syntax", "security", "runtime", "timeout", "provider", "internal"]

# Le stesse cause, ispezionabili a runtime: servono a validare ciò che arriva dal
# sottoprocesso, senza doverne mantenere una seconda copia a mano.
FAILURE_KINDS: tuple[str, ...] = get_args(FailureKind)


@dataclass(frozen=True)
class ExecutionSuccess:
    """Esito riuscito: quello che si mostra all'utente e quello che si dà all'LLM."""

    fig: Any | None      # figura Plotly, oppure None se il codice non ne produce
    value: Any           # DataFrame / Series / scalare / stringa
    summary: str         # riepilogo testuale del risultato, per la spiegazione


@dataclass(frozen=True)
class ExecutionFailure:
    """Esito fallito: la causa (per il codice) e il messaggio (per l'utente)."""

    kind: FailureKind
    message: str
    # Il codice che ha fallito viaggia a parte, non concatenato al messaggio:
    # serve al modello per il tentativo di correzione, non all'utente da leggere.
    code: str = ""

    @property
    def retryable(self) -> bool:
        """
        Vero solo per i fallimenti che un nuovo tentativo del modello può correggere.

        Un errore di sintassi o un'eccezione sui dati sono un difetto del codice
        generato: rigenerarlo ha senso. Un rifiuto della sandbox, un timeout, un
        provider irraggiungibile o un ambiente non disponibile non dipendono dalla
        formulazione del codice: ritentare brucerebbe solo chiamate all'LLM.
        """
        return self.kind in ("syntax", "runtime")


ExecutionResult = ExecutionSuccess | ExecutionFailure
