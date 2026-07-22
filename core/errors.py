"""
Eccezioni applicative.

Un guasto ha bisogno di un TIPO, non di un messaggio: sul tipo si decide (si
ritenta? si mostra? si blocca?), mentre il messaggio serve solo a chi legge. Il
progetto ha già imparato la lezione sul versante dei risultati d'esecuzione, dove
`ExecutionFailure.kind` ha sostituito il prefisso "Errore" di una stringa; qui la
stessa idea vale per ciò che viene sollevato.

`NLDAError` è la radice comune: permette a un chiamante di distinguere un guasto
previsto dall'applicazione da un bug di programmazione, che deve continuare a
propagarsi invece di essere inghiottito da un `except Exception`.
"""


class NLDAError(Exception):
    """Radice di tutti i guasti previsti dall'applicazione."""


class ProviderError(NLDAError):
    """
    La comunicazione con il modello è fallita (rete, credenziali, quota, modello
    inesistente) dopo che il provider ha già esaurito i propri tentativi.

    Distinguerla conta: nessuna riformulazione del prompt la risolve, quindi il
    turno va chiuso subito invece di essere ritentato a spese di altre chiamate.
    """

    def __init__(self, provider: str, original: Exception):
        self.provider = provider
        self.original = original
        super().__init__(f"comunicazione con il provider '{provider}' fallita: {original}")
