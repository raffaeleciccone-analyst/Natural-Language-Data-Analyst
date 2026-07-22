"""
Limiti della demo pubblica.

Il deploy pubblico usa una chiave API del manutentore: senza un tetto, ogni
visitatore spenderebbe il suo credito. La quota vive qui, separata dalla UI, per
due motivi: è una regola di prodotto (non di presentazione) e va poter essere
verificata senza avviare Streamlit.

I limiti sono DUE, e servono a cose diverse:

* quello per SESSIONE tiene onesto il singolo visitatore, ma da solo non
  protegge nulla: basta aprire una scheda nuova per azzerarlo;
* quello GIORNALIERO è il vero tetto di spesa. Vale su tutte le sessioni del
  server e si azzera ogni giorno, così una giornata storta non brucia il credito
  e il giorno dopo la demo è di nuovo viva.

Nessuno dei due è una difesa contro un abuso determinato — per quello servirebbe
un'identità, che una demo pubblica non ha. Servono a contenere il costo, ed è
quello che dichiarano di fare.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DemoLimits:
    """Quota di richieste all'LLM concessa alla demo pubblica."""

    enabled: bool = False
    max_questions: int = 15    # per singola sessione del browser
    max_daily: int = 200       # su tutte le sessioni, azzerato ogni giorno

    def has_budget(self, used_in_session: int, used_today: int = 0) -> bool:
        """True se resta budget su ENTRAMBI i limiti. Fuori dalla demo non c'è limite."""
        if not self.enabled:
            return True
        return used_in_session < self.max_questions and used_today < self.max_daily
