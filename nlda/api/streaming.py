"""
Un turno di analisi trasmesso a pezzi, mentre accade.

## Cosa arriva, e in che ordine

Una domanda impiega da qualche secondo a un minuto: la generazione del codice,
l'esecuzione, e infine la narrazione. Aspettare in silenzio e ricevere tutto
insieme è la versione peggiore della stessa attesa. Qui il turno diventa una
sequenza di eventi:

    step    a che punto è ("Genero il codice…", "Eseguo…")
    result  l'esito COMPLETO tranne la spiegazione: tabella, grafico, codice
    token   un pezzo di spiegazione, mentre il modello la scrive
    done    fine, con il testo completo per chi vuole salvarlo
    error   qualcosa è andato storto prima di poter produrre un risultato

L'ordine non è casuale: `result` arriva **prima** della spiegazione, quindi
tabella e grafico compaiono appena esistono e la prosa li raggiunge dopo. È il
contrario di quello che farebbe una risposta unica, dove il dato più utile
aspetta quello più lento.

## Perché un thread e una coda

`AnalysisService.answer` è sincrona e comunica l'avanzamento con una callback
(`on_step`). Un generatore non può cedere dall'interno di una callback altrui:
quando `on_step` viene invocata, il generatore è fermo dentro `answer`.

La soluzione è far girare `answer` in un thread e usare una coda come canale: la
callback deposita, il generatore preleva e trasmette. Venti righe che evitano di
riscrivere il servizio in asincrono — riscrittura che avrebbe contagiato la
sandbox, i provider e l'app Streamlit, tutti sincroni per ottime ragioni.

## Perché SSE e non WebSocket

Il flusso è a senso unico: il server parla, il client ascolta. Un WebSocket
darebbe un canale bidirezionale che nessuno usa, in cambio di riconnessioni da
gestire a mano e di proxy che a volte lo bloccano. SSE è HTTP: attraversa
qualunque infrastruttura e non aggiunge dipendenze da nessuna delle due parti.
"""
import json
import queue
import threading
from collections.abc import Iterator
from typing import Any

from nlda.log import get_logger
from nlda.results import ExecutionSuccess
from nlda.service import AnalysisService, Turn

log = get_logger(__name__)

# Sentinella: dice al lettore della coda che non arriveranno altri passi. Un
# `None` sarebbe ambiguo — potrebbe essere un messaggio vuoto.
_FINE = object()


def evento(nome: str, dati: Any) -> str:
    """
    Un evento nel formato Server-Sent Events.

    Il doppio a-capo finale NON è estetica: è il delimitatore del protocollo, e
    senza di esso il client tiene l'evento in sospeso aspettandone la fine.
    """
    return f"event: {nome}\ndata: {json.dumps(dati, ensure_ascii=False)}\n\n"


def turno_a_evento(turn: Turn, verso_json) -> dict:
    """L'esito senza la spiegazione: quella arriva dopo, un pezzo alla volta."""
    return verso_json(turn, includi_spiegazione=False)


def trasmetti(service: AnalysisService, question: str, df, *,
              unit: str, verso_json, explain: bool = True) -> Iterator[str]:
    """
    Genera gli eventi di un turno completo.

    `verso_json` traduce il `Turn` nella forma che l'API espone: si passa dal
    di fuori per non far conoscere a questo modulo i modelli Pydantic — qui c'è
    solo la meccanica del flusso.

    Con `explain=False` il flusso si ferma al risultato: niente `token`, e
    soprattutto **nessuna seconda chiamata al modello**. Prima il parametro
    esisteva nella richiesta ma questa rotta lo ignorava, quindi chi dichiarava
    di non volere la narrazione la pagava lo stesso — sulla demo pubblica, dal
    budget condiviso di tutti.
    """
    passi: queue.Queue = queue.Queue()
    esito: dict[str, Any] = {}

    def lavora() -> None:
        try:
            # explain=False: la spiegazione NON la fa il servizio in un colpo
            # solo, la si streamma dopo. Chiederla qui vanificherebbe l'attesa
            # che stiamo cercando di rendere sopportabile.
            esito["turn"] = service.answer(question, df, explain=False, unit=unit,
                                           on_step=passi.put)
        except Exception as e:  # noqa: BLE001 — qualunque guasto va riferito al client
            esito["errore"] = e
        finally:
            passi.put(_FINE)

    thread = threading.Thread(target=lavora, name="turno-streaming", daemon=True)
    thread.start()

    # Finché il lavoro procede, si trasmette l'avanzamento appena arriva.
    while True:
        passo = passi.get()
        if passo is _FINE:
            break
        yield evento("step", {"message": passo})

    thread.join()
    # Si lasciano andare thread e closure PRIMA della parte lenta: gli eventi
    # `token` sono lo streaming della spiegazione, decine di secondi, e in tutto
    # quel tempo la closure terrebbe in vita il DataFrame (piu' la copia filtrata,
    # che nel magazzino non c'e'). Non era una perdita illimitata — alla fine del
    # generatore si liberava — ma "tenuto troppo a lungo" moltiplicato per gli
    # stream simultanei e' RAM vera.
    del thread, lavora

    if "errore" in esito:
        log.error("stream_turno_fallito", extra={"errore": str(esito["errore"])})
        yield evento("error", {"detail": "Errore durante l'analisi. Riprova."})
        return

    turn: Turn = esito["turn"]
    yield evento("result", turno_a_evento(turn, verso_json))

    if not explain or not isinstance(turn.result, ExecutionSuccess):
        # Due motivi per chiudere qui, con lo stesso effetto: il client non vuole
        # la narrazione, oppure non c'è un risultato da raccontare — e a un
        # fallimento non si chiede un commento al modello.
        yield evento("done", {"answer": None})
        return

    pezzi: list[str] = []
    try:
        for pezzo in service.stream_explanation(question, turn.result, unit):
            pezzi.append(pezzo)
            yield evento("token", {"text": pezzo})
    except Exception as e:  # noqa: BLE001 — il risultato è già arrivato: non si perde
        log.warning("stream_spiegazione_fallita", extra={"errore": str(e)})
        yield evento("error", {"detail": "Il risultato c'è, ma la spiegazione non è arrivata."})

    # Il testo completo anche in `done`: chi salva la conversazione non deve
    # ricucire i pezzi, e chi si è collegato tardi non resta con metà frase.
    yield evento("done", {"answer": "".join(pezzi) or None})
