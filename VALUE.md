# Perché questo progetto

> Portare un LLM in produzione sul serio: **il modello racconta, Pandas calcola**;
> il codice generato è validato e isolato; ogni risposta è **tracciata nel costo** e
> **verificabile** nelle colonne che usa; e i limiti sono **dichiarati, non nascosti**.

## Il problema

Le stesse domande sui dati tornano ogni settimana — *"qual è il mese migliore?",
"come vanno le vendite per regione?", "quanto incide il primo cliente?"* — e ogni
volta qualcuno riscrive una query. Un LLM potrebbe rispondere in linguaggio
naturale, ma da solo ha un difetto che in azienda è inaccettabile: **inventa numeri
plausibili**. Sbaglia in modo che *sembra* giusto.

## La soluzione: *Pandas calcola, l'AI racconta*

Il modello **non produce numeri**: genera codice Pandas che li calcola, e poi riceve
i numeri già calcolati per commentarli. Un LLM che "stima" un totale sbaglia in modo
invisibile; Pandas che lo somma o è giusto o solleva un'eccezione. La domanda in
linguaggio naturale resta l'interfaccia; la **correttezza** la garantisce il codice.

Al caricamento di un dataset si ottiene subito un report (KPI, classifiche,
andamento, correlazioni, insight), e da lì si conversa: domande, grafici, confronti
tra periodi, filtri e join tra file — con una risposta testuale che interpreta i
numeri, mai che li produce.

## Perché è costruito così (e perché conta)

Il valore non è "un altro tool per interrogare i dati": è il **come**. Le stesse
scelte che rendono l'app difendibile in un contesto reale sono quelle che un
progetto-giocattolo salta.

- **Sicurezza a strati.** Il codice arriva da un LLM: è non fidato. Viene validato
  staticamente (allowlist di nodi AST, *default-deny*), eseguito in un sottoprocesso
  isolato con timeout, e confinato dal container. Con il modello di minaccia
  esplicito e i rischi residui dichiarati → [THREAT_MODEL.md](THREAT_MODEL.md).
- **Numeri veri, e verificabili.** Oltre a calcolarli con Pandas, ogni risposta
  mostra **su quali colonne poggia** e segnala i risultati sospetti (una quota fuori
  da 0–100%, un valore NaN): l'utente può *fidarsi o verificare*, non deve credere
  sulla parola.
- **Consapevolezza del costo.** Ogni chiamata al modello è tracciata in token e in
  **dollari stimati**, correlata a un `turn_id`: si sa quanto costa una domanda e
  quanto costerebbe scalarla. È il linguaggio con cui un'azienda decide.
- **Onestà sui limiti.** Dove la sandbox è una barriera di processo e non di sistema,
  dove su Windows manca il cap di RAM, dove la correttezza dipende comunque dal
  modello: è scritto, non nascosto. Un limite dichiarato è parte della soluzione.

Le decisioni e i loro trade-off in dettaglio: [ARCHITECTURE.md](ARCHITECTURE.md).

## In sintesi

Un'app che risponde ai dati in linguaggio naturale è facile da *demoare* e difficile
da *rendere affidabile*. Questo progetto sceglie la seconda cosa: la correttezza dei
numeri, la sicurezza del codice generato, la tracciabilità del costo e l'onestà sui
limiti — cioè ciò che serve perché uno strumento del genere si possa davvero mettere
nelle mani di qualcuno.
