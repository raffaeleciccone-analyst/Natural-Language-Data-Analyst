"""
I dataset di esempio: quelli con cui si può provare l'app senza avere un file.

## Perché si scoprono invece di essere elencati

Il catalogo dichiara cosa il progetto *conosce*, ma `disponibili()` restituisce
solo ciò che è davvero sul disco. Serve perché un'installazione può non avere
tutti i file — un fork che alleggerisce il repo, un'immagine costruita con un
`data/` parziale — e un elenco fisso prometterebbe all'utente un pulsante che
poi dà 404.

La conseguenza voluta: aggiungere un esempio è mettere il file in `data/` e una
riga nel catalogo; toglierlo è cancellare il file.

`films.json` è un SOTTOINSIEME della fonte pubblica (vega-datasets), ristretto a
un decennio: come si ottiene sta in `scripts/prepara_dataset_film.py`, insieme al
perché. Un file di dati committato senza la ricetta che lo produce è un file che
nessuno sa piu' rigenerare.
"""
from dataclasses import dataclass
from pathlib import Path

from nlda.log import get_logger

log = get_logger(__name__)

_CARTELLA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class DatasetDemo:
    """Un dataset di esempio: come si chiama il file e come lo si presenta."""

    nome: str          # identificativo stabile, usato dall'API e dalla UI
    file: str
    etichetta: str
    descrizione: str

    @property
    def percorso(self) -> Path:
        return _CARTELLA / self.file

    def esiste(self) -> bool:
        return self.percorso.is_file()


# L'ordine è quello in cui compaiono: il primo è il predefinito.
CATALOGO: tuple[DatasetDemo, ...] = (
    DatasetDemo(
        nome="sales",
        file="sales.csv",
        etichetta="Vendite (Superstore)",
        # La descrizione elenca SOLO colonne che ci sono davvero. Diceva "vendite,
        # sconti e profitto": di sconto e profitto in questo file non c'è traccia,
        # e l'app invitava così a fare proprio le domande a cui non può rispondere
        # — le stesse su cui un modello è tentato di ripiegare su una colonna
        # simile. Una descrizione sbagliata non è un dettaglio di vetrina: è la
        # prima causa delle domande impossibili.
        descrizione="9.800 ordini dal 2015 al 2018: vendite, regione, città, "
                    "categoria di prodotto, segmento di cliente e date di spedizione.",
    ),
    DatasetDemo(
        nome="films",
        file="films.json",
        etichetta="Film (box office)",
        descrizione="1.830 film usciti dal 2000 al 2009: incassi, budget, genere e voti IMDB.",
    ),
)


def disponibili() -> tuple[DatasetDemo, ...]:
    """Quelli il cui file c'è davvero. Mai vuoto in un'installazione sana."""
    presenti = tuple(d for d in CATALOGO if d.esiste())
    if not presenti:
        log.warning("nessun_dataset_di_esempio", extra={"cartella": str(_CARTELLA)})
    return presenti


def trova(nome: str | None) -> DatasetDemo | None:
    """
    Il dataset richiesto, o il primo disponibile se non se ne chiede uno.

    `None` significa "non ce n'è nessuno da dare": chi chiama decide se è un 404
    o un messaggio. Qui non si solleva, perché un dataset di esempio assente è una
    condizione dell'installazione, non un errore di programmazione.
    """
    presenti = disponibili()
    if not presenti:
        return None
    if not nome:
        return presenti[0]
    return next((d for d in presenti if d.nome == nome), None)
