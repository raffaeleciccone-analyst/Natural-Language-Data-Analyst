"""
Test della quota della demo pubblica lato API.

Il deploy pubblico gira con la chiave del manutentore: senza tetto, ogni
visitatore ne spende il credito. Questi test verificano che il tetto ci sia
davvero, perche' e' il genere di protezione che si scopre assente dalla bolletta.

La quota vive in un oggetto di modulo (`app._quota`): ogni test se ne costruisce
uno proprio con `monkeypatch`, cosi' non eredita il conteggio del precedente.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nlda.api import store
from nlda.api.app import app
from nlda.api.quota import Quota, limiti_da_ambiente, visitatore
from nlda.demo import DemoLimits


@pytest.fixture
def client():
    store.magazzino.svuota()
    return TestClient(app)


@pytest.fixture
def csv_bytes() -> bytes:
    return (b"Regione,Vendite,Data\n"
            b"Nord,100,2024-01-15\n"
            b"Sud,200,2024-02-15\n")


def _con_quota(monkeypatch, **limiti) -> Quota:
    """Installa una quota nuova nell'app e la restituisce."""
    q = Quota(DemoLimits(enabled=True, **limiti))
    monkeypatch.setattr("nlda.api.app._quota", q)
    return q


def _domanda(client, dataset_id: str, **kwargs):
    return client.post("/api/ask", json={"dataset_id": dataset_id, "question": "totale?"},
                       **kwargs)


def _domanda_in_streaming(client, dataset_id: str, **kwargs):
    """La stessa domanda dalla rotta che la chat React usa DAVVERO."""
    return client.post("/api/ask/stream",
                       json={"dataset_id": dataset_id, "question": "totale?"}, **kwargs)


@pytest.fixture
def dataset(client, csv_bytes, monkeypatch):
    """Un dataset caricato e un agente finto: nessuna rete verso un modello."""
    monkeypatch.setattr("nlda.api.app.AnalysisService", lambda _a: MagicMock())
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: MagicMock())
    monkeypatch.setattr("nlda.api.app._risposta",
                        lambda *a, **k: {"ok": True, "question": "q", "code": "df",
                                         "value": 1, "value_kind": "scalar"})
    return client.post("/api/dataset",
                       files={"file": ("v.csv", csv_bytes, "text/csv")}).json()["dataset_id"]


# --- Il tetto esiste ----------------------------------------------------------
def test_esaurita_la_quota_personale_si_riceve_429(client, dataset, monkeypatch):
    _con_quota(monkeypatch, max_questions=2, max_daily=100)

    assert _domanda(client, dataset).status_code == 200
    assert _domanda(client, dataset).status_code == 200

    r = _domanda(client, dataset)
    assert r.status_code == 429
    assert "limite della demo" in r.json()["detail"]


def test_anche_la_domanda_in_streaming_scala_la_quota(client, dataset, monkeypatch):
    """
    E' LA rotta della demo: la chat React chiama solo `/ask/stream` (`Chat.tsx`).
    Finche' il tetto valeva sulla sola `/ask`, la demo pubblica spendeva il
    credito del manutentore senza che nulla lo contasse — un tetto che non copre
    la strada percorsa non e' un tetto.
    """
    q = _con_quota(monkeypatch, max_questions=1, max_daily=100)

    assert _domanda_in_streaming(client, dataset).status_code == 200
    assert q.usate_oggi == 1, "la domanda in streaming deve essere contata"

    r = _domanda_in_streaming(client, dataset)
    assert r.status_code == 429
    assert "limite della demo" in r.json()["detail"]


def test_i_due_modi_di_chiedere_spendono_lo_stesso_budget(client, dataset, monkeypatch):
    """Un solo credito, non uno per rotta: alternarle non deve raddoppiarlo."""
    _con_quota(monkeypatch, max_questions=2, max_daily=100)

    assert _domanda(client, dataset).status_code == 200
    assert _domanda_in_streaming(client, dataset).status_code == 200
    assert _domanda(client, dataset).status_code == 429


def test_il_tetto_giornaliero_vale_su_tutti_i_visitatori(client, dataset, monkeypatch):
    """
    E' il limite che protegge davvero il credito: quello personale basta cambiare
    indirizzo per azzerarlo, e infatti qui lo si cambia a ogni richiesta.
    """
    _con_quota(monkeypatch, max_questions=100, max_daily=2)

    for n in range(2):
        r = _domanda(client, dataset, headers={"X-Forwarded-For": f"10.0.0.{n}"})
        assert r.status_code == 200

    r = _domanda(client, dataset, headers={"X-Forwarded-For": "10.0.0.99"})
    assert r.status_code == 429
    assert "budget giornaliero" in r.json()["detail"]


def test_visitatori_diversi_hanno_budget_personali_separati(client, dataset, monkeypatch):
    """Un visitatore che esaurisce il suo non deve chiudere la porta agli altri."""
    _con_quota(monkeypatch, max_questions=1, max_daily=100)

    assert _domanda(client, dataset, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert _domanda(client, dataset, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert _domanda(client, dataset, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


# --- Cosa la quota NON deve toccare -------------------------------------------
def test_chi_porta_la_propria_chiave_non_consuma_il_budget(client, dataset, monkeypatch):
    """Sta spendendo il proprio credito: limitarlo sarebbe un limite senza scopo."""
    q = _con_quota(monkeypatch, max_questions=1, max_daily=100)

    for _ in range(3):
        r = _domanda(client, dataset, headers={"X-API-Key": "sk-mia"})
        assert r.status_code == 200
    assert q.usate_oggi == 0


def test_fuori_dalla_demo_non_c_e_nessun_tetto(client, dataset, monkeypatch):
    monkeypatch.setattr("nlda.api.app._quota", Quota(DemoLimits(enabled=False)))
    for _ in range(5):
        assert _domanda(client, dataset).status_code == 200


def test_le_rotte_che_non_chiamano_il_modello_sono_libere(client, dataset, monkeypatch):
    """
    Report, anteprima e valori distinti li calcola Pandas: non costano nulla al
    manutentore, e contarli renderebbe inutilizzabile la pagina dopo N ricariche.
    """
    _con_quota(monkeypatch, max_questions=0, max_daily=0)   # budget gia' esaurito

    assert client.get(f"/api/dataset/{dataset}/report").status_code == 200
    assert client.get(f"/api/dataset/{dataset}/distinct?column=Regione").status_code == 200
    assert client.post("/api/dataset/demo").status_code == 200


def test_una_sintesi_che_non_parte_non_costa_una_domanda(client, dataset, monkeypatch):
    """
    Senza nulla da riassumere la rotta risponde `text: null` senza chiamare il
    modello. Scalarla comunque farebbe pagare al visitatore una richiesta che non
    ha ricevuto.
    """
    q = _con_quota(monkeypatch, max_questions=5, max_daily=100)
    monkeypatch.setattr("nlda.api.app.analyze", lambda *a, **k: {})

    r = client.post(f"/api/dataset/{dataset}/overview")
    assert r.status_code == 200
    assert r.json()["text"] is None
    assert q.usate_oggi == 0


# --- Il conteggio ------------------------------------------------------------
def test_il_credito_torna_il_giorno_dopo(monkeypatch):
    q = Quota(DemoLimits(enabled=True, max_questions=1, max_daily=1))
    q.consuma("1.1.1.1")
    with pytest.raises(HTTPException) as e:
        q.consuma("1.1.1.1")
    assert e.value.status_code == 429

    # Il processo crede di essere il giorno prima: la rotazione deve azzerare
    # sia il totale del giorno sia i conteggi personali.
    q._giorno = date.today() - timedelta(days=1)
    q.consuma("1.1.1.1")
    assert q.usate_oggi == 1


def test_config_dichiara_la_demo(client, monkeypatch):
    _con_quota(monkeypatch, max_questions=7, max_daily=100)
    c = client.get("/api/config").json()
    assert c["demo_mode"] is True
    assert c["max_questions"] == 7


def test_fuori_dalla_demo_config_non_promette_limiti(client, monkeypatch):
    monkeypatch.setattr("nlda.api.app._quota", Quota(DemoLimits(enabled=False, max_questions=15)))
    c = client.get("/api/config").json()
    assert c["demo_mode"] is False
    assert c["max_questions"] == 0


# --- Chi e' il visitatore -----------------------------------------------------
def test_dietro_un_proxy_si_prende_il_primo_indirizzo_della_catena():
    """
    Su un PaaS `request.client.host` e' il proxy, uguale per tutti: il limite
    personale diventerebbe un secondo limite globale molto piu' stretto.
    """
    finta = MagicMock()
    finta.headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2"}
    assert visitatore(finta) == "203.0.113.7"


def test_senza_intestazione_si_usa_l_indirizzo_della_connessione():
    finta = MagicMock()
    finta.headers = {}
    finta.client.host = "127.0.0.1"
    assert visitatore(finta) == "127.0.0.1"


# --- Configurazione dall'ambiente ---------------------------------------------
def test_i_limiti_arrivano_dalle_stesse_variabili_di_streamlit(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_MAX_QUESTIONS", "9")
    monkeypatch.setenv("DEMO_MAX_DAILY", "300")
    limiti = limiti_da_ambiente()
    assert (limiti.enabled, limiti.max_questions, limiti.max_daily) == (True, 9, 300)


def test_un_valore_malformato_non_toglie_il_tetto(monkeypatch):
    """Il default deve reggere: un refuso nella configurazione non è un permesso."""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_MAX_DAILY", "duecento")
    assert limiti_da_ambiente().max_daily == 200


def test_senza_DEMO_MODE_la_demo_e_spenta(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert limiti_da_ambiente().enabled is False


# --- Provider e modello dall'ambiente -----------------------------------------
def test_senza_PROVIDER_in_locale_si_usa_ollama(monkeypatch):
    from nlda.api.app import _provider_predefinito
    monkeypatch.delenv("PROVIDER", raising=False)
    assert _provider_predefinito() == "ollama"


def test_PROVIDER_decide_il_predefinito(monkeypatch):
    """
    Senza, in deploy ogni domanda falliva: `available_providers()` elenca i
    provider SUPPORTATI, non quelli raggiungibili, quindi la risposta era sempre
    "ollama" — che su un host cloud non esiste.
    """
    from nlda.api.app import _provider_predefinito
    monkeypatch.setenv("PROVIDER", "groq")
    assert _provider_predefinito() == "groq"


def test_un_PROVIDER_sconosciuto_non_rompe_il_servizio(monkeypatch):
    from nlda.api.app import _provider_predefinito
    monkeypatch.setenv("PROVIDER", "provider-inesistente")
    assert _provider_predefinito() == "ollama"


def test_MODEL_vale_per_il_provider_predefinito(monkeypatch):
    from nlda.api.app import _scelta_modello
    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.setenv("MODEL", "llama-3.3-70b-versatile")
    assert _scelta_modello(None, None) == ("groq", "llama-3.3-70b-versatile")


def test_MODEL_non_si_applica_a_un_provider_scelto_dal_client(monkeypatch):
    """Il nome di un modello Groq mandato ad Anthropic e' un 404 che non spiega nulla."""
    from nlda.api.app import _scelta_modello
    monkeypatch.setenv("PROVIDER", "groq")
    monkeypatch.setenv("MODEL", "llama-3.3-70b-versatile")
    assert _scelta_modello("anthropic", None) == ("anthropic", None)


def test_il_modello_chiesto_dal_client_vince_sull_ambiente(monkeypatch):
    from nlda.api.app import _scelta_modello
    monkeypatch.setenv("MODEL", "dall-ambiente")
    assert _scelta_modello(None, "dal-client")[1] == "dal-client"
