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
#   syntax     -> il modello ha prodotto codice non parsificabile (o nessun codice)
#   security   -> la sandbox statica ha rifiutato una costruzione non consentita
#   runtime    -> il codice è valido ma è esploso sui dati (colonna assente, ecc.)
#   dependency -> il codice ha chiesto una libreria che qui non c'è (statsmodels…)
#   timeout    -> esecuzione interrotta perché troppo lenta o troppo pesante
#   provider   -> la comunicazione con il modello è fallita
#   internal   -> l'ambiente di esecuzione isolato non è disponibile o ha risposto male
#
# `dependency` è separato da `runtime` per una ragione misurata: una libreria
# assente non c'è nemmeno al secondo tentativo, quindi rigenerare il codice
# brucia tre chiamate all'LLM — pagate, sulla demo — senza alcuna possibilità di
# riuscire. È lo stesso errore di classificazione già costato caro sul worker che
# non parte.
FailureKind = Literal["syntax", "security", "runtime", "dependency",
                      "timeout", "provider", "internal"]

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


# Cosa può farci l'UTENTE, per ogni causa. Il `message` di un fallimento descrive
# il guasto ("uso di 'open' non consentito"); questo dice come uscirne.
#
# Sta qui, accanto a `FailureKind` da cui deriva già `retryable`, e non nel
# componente che lo mostra: viveva solo nel client React, quindi la stessa
# sandbox che rifiutava lo stesso codice dava un consiglio in un'interfaccia e
# il testo grezzo nell'altra.
ADVICE: dict[str, str] = {
    "security": "La richiesta produrrebbe codice non consentito dalla sandbox. "
                "Riformulala come domanda sui dati.",
    "syntax": "Il modello non è riuscito a produrre codice valido. "
              "Prova a essere più specifico.",
    "runtime": "Il codice è stato eseguito ma è fallito sui dati: "
               "forse una colonna non esiste con quel nome.",
    "dependency": "Il codice generato ha chiesto una libreria non installata "
                  "(tipicamente una linea di tendenza, che richiede statsmodels). "
                  "Rifai la domanda senza chiedere una regressione.",
    "timeout": "L'elaborazione ha superato il tempo massimo. "
               "Prova a restringere la domanda.",
    "provider": "Il modello non è raggiungibile. Controlla la configurazione e riprova.",
    "internal": "L'ambiente di esecuzione isolato non è disponibile.",
}


def advice_for(kind: str) -> str:
    """Il consiglio per una causa, o una frase neutra per una causa ignota."""
    return ADVICE.get(kind, "La richiesta non è andata a buon fine.")


ExecutionResult = ExecutionSuccess | ExecutionFailure
