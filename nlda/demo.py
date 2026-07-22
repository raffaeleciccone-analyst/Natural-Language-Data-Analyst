"""
Limiti della demo pubblica.

Il deploy pubblico usa una chiave API del manutentore: senza un tetto, ogni
visitatore spenderebbe il suo credito. La quota vive qui, separata dalla UI, per
due motivi: è una regola di prodotto (non di presentazione) e va poter essere
verificata senza avviare Streamlit.

Limite noto e dichiarato: il conteggio è per SESSIONE, quindi si azzera aprendo
una nuova scheda. Contiene l'uso distratto, non un abuso deliberato; un tetto
globale richiederebbe uno stato condiviso fra le sessioni.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DemoLimits:
    """Quota di richieste all'LLM concessa a una sessione della demo pubblica."""

    enabled: bool = False
    max_questions: int = 15

    def has_budget(self, used: int) -> bool:
        """True se resta budget. Fuori dalla demo non c'è alcun limite."""
        return not self.enabled or used < self.max_questions
