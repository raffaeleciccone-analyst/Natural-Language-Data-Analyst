"""
Test del catalogo dei dataset di esempio.

Il punto delicato è che l'elenco riflette il DISCO e non il codice: un esempio
che non è stato scaricato non deve comparire come opzione, altrimenti l'utente
clicca un pulsante che risponde 404.
"""
import pytest
from fastapi.testclient import TestClient

from nlda.api import store
from nlda.api.app import app
from nlda.demo_data import CATALOGO, DatasetDemo, disponibili, trova


@pytest.fixture
def client():
    store.magazzino.svuota()
    return TestClient(app)


def test_il_catalogo_elenca_solo_i_file_presenti(monkeypatch, tmp_path):
    finto = DatasetDemo("fantasma", "non-esiste.csv", "Fantasma", "un file che non c'e'")
    monkeypatch.setattr("nlda.demo_data.CATALOGO", (*CATALOGO, finto))
    assert "fantasma" not in [d.nome for d in disponibili()]


def test_sales_c_e_sempre():
    """È versionato nel repo: se sparisce, l'app non ha piu' un esempio."""
    assert "sales" in [d.nome for d in disponibili()]


def test_senza_nome_si_prende_il_primo():
    assert trova(None) is disponibili()[0]


def test_un_nome_sconosciuto_non_ripiega_sul_primo():
    """
    Ripiegare in silenzio darebbe all'utente un dataset diverso da quello chiesto,
    con i numeri di un altro file — meglio un 404 che dice cosa è successo.
    """
    assert trova("inesistente") is None


# --- L'API ---------------------------------------------------------------------
def test_config_elenca_i_dataset_di_esempio(client):
    esempi = client.get("/api/config").json()["demo_datasets"]
    assert [d["name"] for d in esempi] == [d.nome for d in disponibili()]
    assert all(d["label"] and d["description"] for d in esempi)


def test_la_rotta_demo_accetta_un_nome(client):
    r = client.post("/api/dataset/demo?nome=sales")
    assert r.status_code == 200
    assert "Vendite" in r.json()["label"]


def test_senza_nome_la_rotta_demo_funziona_come_prima(client):
    """Retrocompatibilita': il pulsante che non passa un nome deve continuare a valere."""
    assert client.post("/api/dataset/demo").status_code == 200


def test_un_esempio_inesistente_e_un_404(client):
    r = client.post("/api/dataset/demo?nome=fantasma")
    assert r.status_code == 404
    assert "fantasma" in r.json()["detail"]


def test_due_esempi_diversi_non_si_sovrascrivono(client, monkeypatch):
    """
    L'impronta include il nome del file: senza, il secondo dataset finirebbe sulla
    chiave del primo e il magazzino restituirebbe quello sbagliato.
    """
    from nlda.api import store as magazzino
    a = client.post("/api/dataset/demo?nome=sales").json()
    monkeypatch.setattr("nlda.demo_data.CATALOGO", CATALOGO)
    b = magazzino.impronta(b"__demo__", "films.json")
    assert a["dataset_id"] != b


def test_il_dataset_di_esempio_si_legge_una_volta_sola(client, monkeypatch):
    """
    Veniva riletto e ri-analizzato da disco a OGNI visita, anche se identico e
    gia' in memoria: misurato sul deploy, 9,5 secondi per ogni utente che apriva
    la pagina — piu' di sette volte il report che ne segue.
    """
    from nlda.api import app as modulo

    letture: list[str] = []
    vero = modulo.load_dataset

    def conta(file_name="sales.csv"):
        letture.append(file_name)
        return vero(file_name)

    monkeypatch.setattr(modulo, "load_dataset", conta)

    primo = client.post("/api/dataset/demo?nome=sales").json()
    secondo = client.post("/api/dataset/demo?nome=sales").json()

    assert letture == ["sales.csv"], f"letto {len(letture)} volte invece di una"
    assert primo["dataset_id"] == secondo["dataset_id"]
    assert primo["rows"] == secondo["rows"]


def test_esempi_diversi_si_leggono_entrambi(client, monkeypatch):
    """La cache e' per FILE: chiedere l'altro esempio deve leggerlo davvero."""
    from nlda.api import app as modulo

    letture: list[str] = []
    vero = modulo.load_dataset
    monkeypatch.setattr(modulo, "load_dataset",
                        lambda f="sales.csv": (letture.append(f), vero(f))[1])

    client.post("/api/dataset/demo?nome=sales")
    client.post("/api/dataset/demo?nome=films")
    assert letture == ["sales.csv", "films.json"]
