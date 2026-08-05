"""
Test dell'API HTTP.

Nessuna rete verso un modello: le rotte che chiamano l'LLM ricevono un agente
finto. Cio' che si verifica e' il CONTRATTO — forma della risposta, codici di
stato, e soprattutto la distinzione fra "errore del servizio" (4xx/5xx) ed
"esito negativo dell'operazione" (200 con ok:false), che e' la scelta di
progetto piu' facile da rompere per distrazione.
"""
import io
import json
import logging
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from nlda.api import cache, store
from nlda.api.app import _colonna, app
from nlda.results import ExecutionFailure, ExecutionSuccess
from nlda.service import Turn


@pytest.fixture
def client():
    store.magazzino.svuota()
    # Anche i ricordi dei report: senza, un test che carica gli stessi byte di
    # un altro riceverebbe la risposta calcolata la' — stesso contenuto, stesso
    # identificativo, stessa chiave. Verissimo in produzione, veleno fra due test
    # che si aspettano scelte diverse.
    cache.ricordi.svuota()
    return TestClient(app)


@pytest.fixture
def csv_bytes() -> bytes:
    return (b"Regione,Vendite,Data\n"
            b"Nord,100,2024-01-15\n"
            b"Sud,200,2024-02-15\n"
            b"Nord,150,2024-03-15\n"
            b"Sud,250,2024-04-15\n")


# --- Servizio -----------------------------------------------------------------
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_config_elenca_i_provider(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    j = r.json()
    assert j["providers"], "almeno un provider deve essere disponibile"
    assert {"name", "default_model", "requires_api_key"} <= set(j["providers"][0])
    assert "csv" in j["supported_extensions"]


def test_la_config_dichiara_ENTRAMBI_i_limiti_sul_file(client):
    """
    Il tetto di upload e quello di memoria sono limiti diversi, e il secondo non
    si deduce dal primo: un CSV piccolo su disco puo' essere enorme in memoria.
    Chi lo legge da fuori — il frontend, e lo script che verifica il deploy — non
    ha altro modo di sapere cosa questa installazione concede davvero.
    """
    from nlda.config import settings
    from nlda.loader import tetto_upload_byte

    j = client.get("/api/config").json()
    assert j["max_upload_mb"] == settings.max_upload_mb
    assert j["max_dataset_ram_mb"] == settings.max_dataset_ram_mb
    # Il tetto DICHIARATO qui e quello IMPOSTO dal caricatore sono lo stesso
    # valore: erano due — uno scritto nella rotta, uno nella configurazione — e
    # un deploy che ne cambiasse uno avrebbe annunciato un limite diverso da
    # quello davvero applicato.
    assert tetto_upload_byte() == j["max_upload_mb"] * 1024 * 1024


def test_lo_schema_openapi_si_genera(client):
    """Se lo schema non si genera, il frontend non puo' derivarne i tipi."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    percorsi = r.json()["paths"]
    for atteso in ["/api/config", "/api/dataset", "/api/ask", "/api/project-qa"]:
        assert atteso in percorsi


def test_l_API_dichiara_la_versione_del_pacchetto(client):
    """
    Una sola fonte: la versione stava scritta a mano qui E in `pyproject`, e due
    copie di un numero che deve cambiare insieme prima o poi divergono — con
    l'API che dichiara nello schema una versione diversa da quella distribuita.
    """
    import nlda

    assert client.get("/openapi.json").json()["info"]["version"] == nlda.__version__


# --- Dataset ------------------------------------------------------------------
def test_carica_un_csv(client, csv_bytes):
    r = client.post("/api/dataset",
                    files={"file": ("vendite.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200
    j = r.json()
    assert j["rows"] == 4 and j["columns"] == 3
    assert "Vendite" in j["measures"]
    assert j["suggested_measure"] == "Vendite"
    assert j["dataset_id"]


def test_lo_stesso_file_non_occupa_due_volte_la_memoria(client, csv_bytes):
    file = {"file": ("vendite.csv", csv_bytes, "text/csv")}
    primo = client.post("/api/dataset", files=file).json()
    secondo = client.post("/api/dataset",
                          files={"file": ("vendite.csv", csv_bytes, "text/csv")}).json()
    assert primo["dataset_id"] == secondo["dataset_id"]
    assert len(store.magazzino) == 1


def test_la_percentuale_di_mancanti_e_un_numero(client):
    """Non la stringa formattata di `profile()`: un client non deve parsificare."""
    dati = b"A,B\n1,\n2,x\n"
    j = client.post("/api/dataset", files={"file": ("m.csv", dati, "text/csv")}).json()
    per_nome = {c["name"]: c for c in j["profile"]}
    assert isinstance(per_nome["B"]["missing_pct"], (int, float))
    assert per_nome["B"]["missing_pct"] == pytest.approx(50.0)


def test_un_file_illeggibile_da_400_non_500(client):
    r = client.post("/api/dataset",
                    files={"file": ("rotto.xlsx", b"non e' un excel", "application/vnd.ms-excel")})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_dataset_di_esempio(client):
    j = client.post("/api/dataset/demo").json()
    assert j["rows"] > 1000
    assert j["suggested_measure"]


# --- Report -------------------------------------------------------------------
def test_report(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report")
    assert r.status_code == 200
    j = r.json()
    assert len(j["kpis"]) >= 1
    assert j["kpis"][0]["value"], "il valore arriva gia' formattato"
    assert len(j["preview"]) == 4


def test_il_report_e_serializzabile_in_json(client):
    """
    Le figure Plotly contengono array numpy e i DataFrame contengono NaN e
    Timestamp: nessuno dei due e' JSON valido senza conversione. E' il difetto che
    ha rotto questa rotta la prima volta.
    """
    d = client.post("/api/dataset/demo").json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report")
    assert r.status_code == 200
    testo = r.text
    assert "NaN" not in testo, "NaN letterale non e' JSON valido"
    json.loads(testo)   # deve rileggersi


def test_un_dataset_con_valori_infiniti_non_fa_500(client):
    """
    Trovato provando a rompere la demo il 5 agosto 2026: un CSV con `inf` fra i
    valori faceva rispondere 500 all'intero report. Il 500 dice "il servizio e'
    guasto" — e qui lo era davvero: `fmt_num` chiamava `int()` su un infinito.

    Il test sta al livello dell'API e non su `fmt_num` (dove c'e' il suo) perche'
    e' qui che il difetto si e' manifestato: la catena analyze -> insight -> KPI
    passa da tre moduli, e uno solo di essi basta a riportare l'errore.
    """
    dati = b"Regione,Vendite\nNord,1e308\nSud,-1e308\nOvest,inf\nEst,NaN\n"
    d = client.post("/api/dataset", files={"file": ("estremi.csv", dati, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report")
    assert r.status_code == 200
    json.loads(r.text)   # e il JSON deve rileggersi: `Infinity` non e' JSON valido


def test_report_di_un_dataset_inesistente_da_404(client):
    r = client.get("/api/dataset/inventato/report")
    assert r.status_code == 404
    assert "ricaricalo" in r.json()["detail"]


# --- Parametri di colonna -----------------------------------------------------
# Un nome di colonna arriva dalla querystring, cioe' da fuori: valeva 500 per
# quattro strade diverse. Il 500 dice "il servizio e' guasto"; qui il servizio sta
# benissimo ed e' la richiesta a essere sbagliata.
def test_una_misura_testuale_da_400_non_500(client, csv_bytes):
    """Esisteva ma non era numerica: `build_kpis` ne faceva la media."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report", params={"measure": "Regione"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "non contiene numeri" in detail
    assert "'Vendite'" in detail, "il messaggio deve dire quali misure ci sono"


def test_una_misura_inesistente_da_400(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report", params={"measure": "Inventata"})
    assert r.status_code == 400
    assert "measure" in r.json()["detail"]


def test_una_categoria_inesistente_da_400(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report", params={"category": "Boh"})
    assert r.status_code == 400
    assert "category" in r.json()["detail"]


def test_gli_spazi_ai_bordi_del_nome_si_tollerano(client, csv_bytes):
    """`?measure=Vendite ` e' un URL scritto a mano, non una colonna diversa."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report", params={"measure": "Vendite "})
    assert r.status_code == 200
    assert r.json()["measure"] == "Vendite"


def test_la_colonna_che_si_chiama_davvero_con_lo_spazio_vince():
    """
    La tolleranza e' un RIPIEGO, non una normalizzazione: se il dataset ha davvero
    una colonna `'Vendite '`, chi la chiede per nome deve ricevere quella. Provato
    sull'helper perche' un CSV con due colonne cosi' simili non e' scrivibile in
    modo credibile passando dal caricatore.
    """
    df = pd.DataFrame({"Vendite ": [1, 2], "Vendite": [10, 20]})
    assert _colonna(df, "measure", "Vendite ") == "Vendite "
    assert _colonna(df, "measure", "Vendite") == "Vendite"


def test_una_misura_vuota_ricade_sul_default(client, csv_bytes):
    """`?measure=` non e' una colonna sbagliata: e' nessuna scelta."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report", params={"measure": ""})
    assert r.status_code == 200
    assert r.json()["measure"] == "Vendite"


def test_la_sintesi_rifiuta_la_misura_sbagliata_senza_chiamare_il_modello(
        client, csv_bytes, monkeypatch):
    """
    Il parametro si valida PRIMA della quota e del provider: altrimenti una misura
    sbagliata costava al visitatore una domanda del budget e produceva una sintesi
    che quel parametro l'aveva ignorato — cioe' una risposta a una domanda diversa.
    """
    def _vietato(**_):
        raise AssertionError("il modello non deve essere chiamato")

    monkeypatch.setattr("nlda.api.app.DataAgent", _vietato)
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.post(f"/api/dataset/{d['dataset_id']}/overview", params={"measure": "Regione"})
    assert r.status_code == 400


# --- Domande ------------------------------------------------------------------
def _finto_servizio(monkeypatch, turn: Turn):
    servizio = MagicMock()
    servizio.answer.return_value = turn
    monkeypatch.setattr("nlda.api.app.AnalysisService", lambda _a: servizio)
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: MagicMock())
    return servizio


def test_ask_riuscita(client, csv_bytes, monkeypatch):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _finto_servizio(monkeypatch, Turn(
        question="totale?", code="risultato = df['Vendite'].sum()",
        result=ExecutionSuccess(fig=None, value=700, summary="700"),
        explanation="Il totale è 700."))

    r = client.post("/api/ask", json={"dataset_id": d["dataset_id"], "question": "totale?"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["value"] == 700 and j["value_kind"] == "scalar"
    assert j["answer"] == "Il totale è 700."
    assert "Vendite" in j["columns_used"], "le colonne toccate fanno parte della risposta"


def test_avviso_anti_allucinazione_arriva_al_client(client, csv_bytes, monkeypatch):
    """
    Se la domanda nomina una colonna inesistente e il modello ripiega su una reale,
    l'API deve dirlo nei `warnings` — la stessa protezione che Streamlit aveva e che
    la demo React non riceveva. La colonna «Fatturato» non esiste; il codice usa
    Vendite: l'avviso va emesso e deve citare la colonna su cui ci si è basati.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _finto_servizio(monkeypatch, Turn(
        question="qual è il totale della colonna Fatturato?",
        code="risultato = df['Vendite'].sum()",
        result=ExecutionSuccess(fig=None, value=700, summary="700"),
        explanation="Il totale è 700."))

    j = client.post("/api/ask", json={"dataset_id": d["dataset_id"],
                                      "question": "qual è il totale della colonna Fatturato?"}).json()
    assert j["ok"] is True
    assert any("«Fatturato»" in a for a in j["warnings"]), j["warnings"]
    assert any("Vendite" in a for a in j["warnings"])


def test_avviso_anti_allucinazione_anche_quando_il_codice_fallisce(client, csv_bytes, monkeypatch):
    """L'avviso vale anche in caso di fallimento: la colonna sostituita può far
    fallire il codice tanto quanto farlo riuscire, e l'utente deve saperlo comunque."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _finto_servizio(monkeypatch, Turn(
        question="somma della colonna Sconto",
        code="risultato = df['Sconto'].sum()",
        result=ExecutionFailure("runtime", "colonna 'Sconto' non presente")))

    j = client.post("/api/ask", json={"dataset_id": d["dataset_id"],
                                      "question": "somma della colonna Sconto"}).json()
    assert j["ok"] is False
    assert any("«Sconto»" in a for a in j["warnings"]), j["warnings"]


def test_domanda_pulita_non_produce_avvisi(client, csv_bytes, monkeypatch):
    """Nessuna colonna inventata: `warnings` resta vuoto (niente falsi allarmi)."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _finto_servizio(monkeypatch, Turn(
        question="totale delle vendite", code="risultato = df['Vendite'].sum()",
        result=ExecutionSuccess(fig=None, value=700, summary="700")))

    j = client.post("/api/ask", json={"dataset_id": d["dataset_id"],
                                      "question": "totale delle vendite"}).json()
    assert j["warnings"] == []


def test_un_fallimento_non_e_un_errore_http(client, csv_bytes, monkeypatch):
    """
    Un codice rifiutato dalla sandbox e' un ESITO, non un guasto del servizio:
    200 con ok:false e la CAUSA in failure_kind. Se diventasse un 500, un client
    non saprebbe distinguerlo da un servizio rotto — e ritenterebbe.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _finto_servizio(monkeypatch, Turn(
        question="apri un file", code="open('/etc/passwd')",
        result=ExecutionFailure("security", "uso di 'open' non consentito")))

    r = client.post("/api/ask", json={"dataset_id": d["dataset_id"], "question": "apri un file"})
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is False
    assert j["failure_kind"] == "security"
    assert "open" in j["message"]


def test_ask_restituisce_una_tabella_serializzabile(client, csv_bytes, monkeypatch):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    tabella = pd.DataFrame({"Regione": ["Nord", "Sud"], "Vendite": [250.0, float("nan")]})
    _finto_servizio(monkeypatch, Turn(
        question="per regione", code="risultato = df.groupby('Regione')['Vendite'].sum()",
        result=ExecutionSuccess(fig=None, value=tabella, summary="")))

    j = client.post("/api/ask",
                    json={"dataset_id": d["dataset_id"], "question": "per regione"}).json()
    assert j["value_kind"] == "table"
    assert j["value"][0]["Regione"] == "Nord"
    assert j["value"][1]["Vendite"] is None, "NaN deve diventare null, non 'NaN'"


def test_ask_su_dataset_inesistente_da_404(client):
    r = client.post("/api/ask", json={"dataset_id": "boh", "question": "totale?"})
    assert r.status_code == 404


def test_una_domanda_vuota_e_respinta_dalla_validazione(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.post("/api/ask", json={"dataset_id": d["dataset_id"], "question": ""})
    assert r.status_code == 422


def test_provider_sconosciuto_da_400(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.post("/api/ask", json={"dataset_id": d["dataset_id"],
                                      "question": "x", "provider": "inventato"})
    assert r.status_code == 400


# --- Domande sul progetto -----------------------------------------------------
def test_project_qa_cita_le_fonti(client, monkeypatch):
    from nlda.project_qa import Frammento
    monkeypatch.setattr("nlda.api.app.get_provider", lambda *a, **k: MagicMock())
    monkeypatch.setattr("nlda.api.app.project_answer",
                        lambda p, q: ("La sandbox usa una allowlist.",
                                      [Frammento("Documentazione tecnica", "8. La sandbox", "…")]))
    r = client.post("/api/project-qa", json={"question": "come funziona la sandbox?"})
    assert r.status_code == 200
    j = r.json()
    assert j["answer"].startswith("La sandbox")
    assert j["sources"] == ["Documentazione tecnica — 8. La sandbox"]


# --- Magazzino ----------------------------------------------------------------
def test_il_magazzino_sfratta_il_meno_usato():
    m = store.MagazzinoDataset(capienza=2)
    for i in range(3):
        m.aggiungi(f"k{i}", pd.DataFrame({"a": [i]}), f"f{i}")
    assert len(m) == 2
    assert m.prendi("k0") is None, "il piu' vecchio e' stato sfrattato"
    assert m.prendi("k2") is not None


def _tabella(mega: float) -> pd.DataFrame:
    """Una tabella di circa `mega` MB: 8 byte a riga, una colonna di interi."""
    return pd.DataFrame({"a": range(int(mega * 1024 * 1024 / 8))})


def test_il_magazzino_sfratta_anche_quando_e_la_RAM_a_finire():
    """
    Il tetto sul NUMERO di tabelle non e' un tetto di memoria: otto dataset entro
    i limiti del caricatore stanno in otto, ma non in 2 GB. Qui la capienza e'
    larga apposta — a sfrattare deve essere il conto dei byte.
    """
    m = store.MagazzinoDataset(capienza=100, ram_mb=4)
    for i in range(4):
        m.aggiungi(f"k{i}", _tabella(1.5), f"f{i}")
    assert m.byte_totali() <= 4 * 1024 * 1024
    assert m.prendi("k0") is None and m.prendi("k1") is None
    assert m.prendi("k3") is not None, "l'ultima caricata deve restare"


def test_lo_sfratto_per_memoria_segue_l_uso_non_l_ordine_di_arrivo():
    m = store.MagazzinoDataset(capienza=100, ram_mb=4)
    m.aggiungi("vecchia", _tabella(1.5), "a")
    m.aggiungi("mezzo", _tabella(1.5), "b")
    m.prendi("vecchia")             # rinfresca: ora la meno usata e' "mezzo"
    m.aggiungi("nuova", _tabella(1.5), "c")
    assert m.prendi("mezzo") is None
    assert m.prendi("vecchia") is not None


def test_la_lru_non_dipende_dalla_risoluzione_dell_orologio(monkeypatch):
    """
    Orologio fermo = due usi dentro lo stesso scatto, che su Windows dura ~15 ms.
    Chi e' stato usato per ultimo si sa lo stesso, perche' l'ordine e' contato e
    non cronometrato: qui la vittima giusta e' `k1`, mai toccata dopo l'arrivo.
    """
    monkeypatch.setattr(store.time, "monotonic", lambda: 1000.0)
    m = store.MagazzinoDataset(capienza=2)
    m.aggiungi("k0", pd.DataFrame({"a": [0]}), "f0")
    m.aggiungi("k1", pd.DataFrame({"a": [1]}), "f1")
    m.prendi("k0")
    m.aggiungi("k2", pd.DataFrame({"a": [2]}), "f2")
    assert m.prendi("k1") is None, "sfrattata la voce sbagliata"
    assert m.prendi("k0") is not None


def test_un_dataset_piu_grande_del_tetto_si_tiene_e_lo_si_dichiara(caplog):
    """
    Sfrattare l'unica voce lascerebbe l'utente senza il file che ha appena
    caricato, e la memoria sarebbe occupata lo stesso dal DataFrame ricevuto. Si
    tiene — ma finisce nei log, perche' un tetto tarato male si deve poter vedere.
    """
    m = store.MagazzinoDataset(capienza=100, ram_mb=1)
    registro = logging.getLogger("nlda.api.store")
    with caplog.at_level(logging.WARNING, logger="nlda.api.store"):
        registro.addHandler(caplog.handler)   # il logger del progetto non propaga
        try:
            m.aggiungi("unica", _tabella(3), "grande")
        finally:
            registro.removeHandler(caplog.handler)
    assert m.prendi("unica") is not None
    assert "magazzino_oltre_il_tetto" in caplog.text


def test_fai_spazio_sfratta_per_un_dataset_che_deve_ancora_arrivare():
    """
    Lo sfratto di `aggiungi` arriva troppo tardi per chi sta LEGGENDO: mentre il
    parser lavora, i dataset vecchi occupano ancora la loro memoria e i due costi
    si sommano. Misurato sulla demo: 40 MB in magazzino + 40 MB in arrivo
    uccidevano il container, e nessuno dei due da solo sforava il tetto.
    """
    m = store.MagazzinoDataset(capienza=100, ram_mb=4)
    m.aggiungi("k0", _tabella(1.5), "f0")
    m.aggiungi("k1", _tabella(1.5), "f1")
    usciti = m.fai_spazio(3 * 1024 * 1024)
    assert usciti == 1
    assert m.prendi("k0") is None, "doveva uscire il meno usato di recente"
    assert m.prendi("k1") is not None


def test_fai_spazio_non_sfratta_l_ultima_voce():
    """
    Buttare l'unico dataset rimasto non aiuterebbe chi sta caricando (il posto
    non basterebbe comunque) e toglierebbe i dati a chi li sta usando. Quel caso
    lo ferma il tetto per dataset del caricatore, prima di arrivare qui.
    """
    m = store.MagazzinoDataset(capienza=100, ram_mb=4)
    m.aggiungi("unica", _tabella(1.5), "f")
    assert m.fai_spazio(100 * 1024 * 1024) == 0
    assert m.prendi("unica") is not None


def test_l_upload_fa_posto_PRIMA_di_leggere(client, csv_bytes, monkeypatch):
    """
    L'ordine è tutta la difesa: fare posto dopo la lettura significa averla già
    pagata. Si registra la sequenza reale delle due chiamate.
    """
    from nlda.api import app as modulo

    ordine: list[str] = []
    vero_read_any = modulo.read_any
    monkeypatch.setattr(store.magazzino, "fai_spazio",
                        lambda byte: (ordine.append("spazio"), 0)[1])
    monkeypatch.setattr(modulo, "read_any",
                        lambda f: (ordine.append("lettura"), vero_read_any(f))[1])

    r = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")})
    assert r.status_code == 200
    assert ordine == ["spazio", "lettura"]


def test_i_byte_tornano_indietro_quando_una_voce_scade():
    """Il conto della memoria e' derivato dalle voci: non puo' restare indietro."""
    m = store.MagazzinoDataset(ttl=-1)
    m.aggiungi("k", _tabella(1), "f")
    assert m.prendi("k") is None      # scaduta: rimossa
    assert m.byte_totali() == 0


def test_il_magazzino_scade():
    m = store.MagazzinoDataset(ttl=-1)   # gia' scaduto in partenza
    m.aggiungi("k", pd.DataFrame({"a": [1]}), "f")
    assert m.prendi("k") is None


def test_l_impronta_dipende_solo_dal_contenuto():
    a = store.impronta(b"stessi byte", "nome.csv")
    b = store.impronta(b"stessi byte", "nome.csv")
    c = store.impronta(b"altri byte", "nome.csv")
    assert a == b and a != c


def test_il_file_caricato_conserva_il_nome_per_il_formato():
    """
    `read_any` riconosce il formato dall'estensione: il wrapper deve esporla.

    La classe vive accanto a `read_any`, di cui rispetta il contratto: le due
    interfacce ne avevano una copia a testa.
    """
    from nlda.loader import NamedBytesIO
    f = NamedBytesIO(b"a,b\n1,2\n", "dati.csv")
    assert f.name == "dati.csv"
    assert isinstance(f, io.BytesIO)


# --- Filtro -------------------------------------------------------------------
def test_valori_distinti_per_costruire_il_filtro(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/distinct", params={"column": "Regione"})
    assert r.status_code == 200
    assert r.json()["values"] == ["Nord", "Sud"]
    assert r.json()["truncated"] is False


def test_distinct_di_una_colonna_inesistente_da_400(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/distinct", params={"column": "Inventata"})
    assert r.status_code == 400


def test_il_filtro_restringe_il_report(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    intero = client.get(f"/api/dataset/{d['dataset_id']}/report").json()
    filtrato = client.get(f"/api/dataset/{d['dataset_id']}/report",
                          params={"filter_column": "Regione", "filter_values": ["Nord"]}).json()
    assert len(intero["preview"]) == 4
    assert len(filtrato["preview"]) == 2
    # 100 + 150 = 250 sul Nord, contro 700 sul totale: il KPI deve seguire il filtro
    assert filtrato["kpis"][0]["value"] != intero["kpis"][0]["value"]


def test_il_filtro_vale_anche_per_le_domande(client, csv_bytes, monkeypatch):
    """
    Se restringesse solo il report, l'utente vedrebbe numeri di un sottoinsieme e
    riceverebbe risposte sul totale: due verita' diverse nella stessa pagina.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    visti = {}
    servizio = MagicMock()

    def registra(question, df, **kwargs):
        visti["righe"] = len(df)
        return Turn(question=question, code="x",
                    result=ExecutionSuccess(fig=None, value=1, summary=""))

    servizio.answer.side_effect = registra
    monkeypatch.setattr("nlda.api.app.AnalysisService", lambda _a: servizio)
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: MagicMock())

    client.post("/api/ask", json={"dataset_id": d["dataset_id"], "question": "totale?",
                                  "filtro": {"column": "Regione", "values": ["Nord"]}})
    assert visti["righe"] == 2, "il servizio deve ricevere il df GIA' filtrato"


def test_un_filtro_su_colonna_inesistente_da_400(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/report",
                   params={"filter_column": "Boh", "filter_values": ["x"]})
    assert r.status_code == 400


# --- Confronto tra periodi ----------------------------------------------------
def test_confronto_tra_periodi(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/periods",
                   params={"date_column": "Data", "measure": "Vendite", "freq": "mese"})
    assert r.status_code == 200
    righe = r.json()["rows"]
    assert len(righe) == 4
    assert righe[0]["change_pct"] is None, "il primo periodo non ha un prima"
    assert righe[1]["change_pct"] == pytest.approx(100.0)   # 100 -> 200


def test_periodi_con_misura_testuale_da_400(client, csv_bytes):
    """`compare_periods` divide per il periodo prima: su testo era un TypeError."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/periods",
                   params={"date_column": "Data", "measure": "Regione"})
    assert r.status_code == 400
    assert "non contiene numeri" in r.json()["detail"]


def test_periodi_con_frequenza_ignota_da_400(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/periods",
                   params={"date_column": "Data", "measure": "Vendite", "freq": "settimana"})
    assert r.status_code == 400


def test_colonne_data_disponibili(client, csv_bytes):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    r = client.get(f"/api/dataset/{d['dataset_id']}/date-columns")
    assert r.status_code == 200
    assert "Data" in r.json()


# --- Unione -------------------------------------------------------------------
def test_unione_di_due_dataset(client, csv_bytes):
    a = client.post("/api/dataset", files={"file": ("a.csv", csv_bytes, "text/csv")}).json()
    secondo = b"Regione,Responsabile\nNord,Anna\nSud,Bruno\n"
    b_ = client.post("/api/dataset", files={"file": ("b.csv", secondo, "text/csv")}).json()

    r = client.post("/api/dataset/join", json={
        "left_id": a["dataset_id"], "right_id": b_["dataset_id"],
        "left_on": "Regione", "right_on": "Regione", "how": "inner"})
    assert r.status_code == 200
    unito = r.json()
    assert unito["rows"] == 4
    assert "Responsabile" in [c["name"] for c in unito["profile"]]
    assert unito["dataset_id"] not in (a["dataset_id"], b_["dataset_id"])


def test_la_stessa_unione_non_duplica_la_memoria(client, csv_bytes):
    a = client.post("/api/dataset", files={"file": ("a.csv", csv_bytes, "text/csv")}).json()
    b_ = client.post("/api/dataset",
                     files={"file": ("b.csv", b"Regione,R\nNord,x\n", "text/csv")}).json()
    corpo = {"left_id": a["dataset_id"], "right_id": b_["dataset_id"],
             "left_on": "Regione", "right_on": "Regione"}
    primo = client.post("/api/dataset/join", json=corpo).json()
    secondo = client.post("/api/dataset/join", json=corpo).json()
    assert primo["dataset_id"] == secondo["dataset_id"]


def test_l_unione_che_duplica_le_righe_lo_dice_anche_all_API(client, csv_bytes):
    """
    Lo stesso avviso che vede Streamlit: se arrivasse solo a una delle due
    interfacce, la stessa unione sarebbe giudicata in due modi. La demo React lo
    mostra nel pannello dell'unione.
    """
    a = client.post("/api/dataset", files={"file": ("a.csv", csv_bytes, "text/csv")}).json()
    # Due righe per 'Nord': ogni ordine del Nord si duplichera'.
    doppio = b"Regione,Responsabile\nNord,Anna\nNord,Bruno\nSud,Carla\n"
    b_ = client.post("/api/dataset", files={"file": ("b.csv", doppio, "text/csv")}).json()

    r = client.post("/api/dataset/join", json={
        "left_id": a["dataset_id"], "right_id": b_["dataset_id"],
        "left_on": "Regione", "right_on": "Regione", "how": "inner"})
    assert r.status_code == 200
    unito = r.json()
    assert unito["rows"] == 6, "premessa: 4 righe diventate 6"
    assert unito["warnings"], "l'API deve dire che le righe sono state duplicate"
    assert "gonfiati" in unito["warnings"][0]


def test_un_unione_pulita_non_produce_avvisi(client, csv_bytes):
    a = client.post("/api/dataset", files={"file": ("a.csv", csv_bytes, "text/csv")}).json()
    pulito = b"Regione,Responsabile\nNord,Anna\nSud,Bruno\n"
    b_ = client.post("/api/dataset", files={"file": ("b.csv", pulito, "text/csv")}).json()
    r = client.post("/api/dataset/join", json={
        "left_id": a["dataset_id"], "right_id": b_["dataset_id"],
        "left_on": "Regione", "right_on": "Regione"})
    assert r.json()["warnings"] == []


def test_unione_su_colonna_inesistente_da_400(client, csv_bytes):
    a = client.post("/api/dataset", files={"file": ("a.csv", csv_bytes, "text/csv")}).json()
    r = client.post("/api/dataset/join", json={
        "left_id": a["dataset_id"], "right_id": a["dataset_id"],
        "left_on": "Inventata", "right_on": "Regione"})
    assert r.status_code == 400


# --- Esportazione -------------------------------------------------------------
def test_export_in_markdown(client):
    r = client.post("/api/export", json={
        "dataset_label": "vendite.csv",
        "turns": [{"question": "Qual è il totale?", "code": "df['Vendite'].sum()",
                   "answer": "Il totale è 700.", "value_preview": "700"}]})
    assert r.status_code == 200
    md = r.json()["markdown"]
    assert "Qual è il totale?" in md
    assert "df['Vendite'].sum()" in md
    assert "Il totale è 700." in md


def test_export_di_una_conversazione_vuota_non_esplode(client):
    r = client.post("/api/export", json={"turns": []})
    assert r.status_code == 200
    assert isinstance(r.json()["markdown"], str)


# --- Streaming ----------------------------------------------------------------
def _eventi(testo: str) -> list[tuple[str, dict]]:
    """Spezza un flusso SSE nella sequenza (nome evento, dati)."""
    fuori = []
    for blocco in testo.split("\n\n"):
        if not blocco.strip():
            continue
        nome = corpo = None
        for riga in blocco.splitlines():
            if riga.startswith("event: "):
                nome = riga[7:]
            elif riga.startswith("data: "):
                corpo = json.loads(riga[6:])
        if nome:
            fuori.append((nome, corpo))
    return fuori


def _servizio_streaming(monkeypatch, turn: Turn, pezzi=("Il ", "totale ", "è 700.")):
    servizio = MagicMock()

    def risponde(question, df, **kwargs):
        # `on_step` e' come il servizio comunica l'avanzamento: lo si esercita,
        # perche' e' proprio quel canale che lo streaming deve trasformare in eventi.
        passo = kwargs.get("on_step")
        if passo:
            passo("Genero il codice…")
            passo("Eseguo il codice…")
        return turn

    servizio.answer.side_effect = risponde
    servizio.stream_explanation.return_value = iter(pezzi)
    monkeypatch.setattr("nlda.api.app.AnalysisService", lambda _a: servizio)
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: MagicMock())
    return servizio


def test_lo_streaming_manda_avanzamento_risultato_e_testo(client, csv_bytes, monkeypatch):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _servizio_streaming(monkeypatch, Turn(
        question="totale?", code="risultato = df['Vendite'].sum()",
        result=ExecutionSuccess(fig=None, value=700, summary="700")))

    r = client.post("/api/ask/stream",
                    json={"dataset_id": d["dataset_id"], "question": "totale?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    ev = _eventi(r.text)
    nomi = [n for n, _ in ev]
    assert nomi.count("step") == 2
    assert nomi.index("result") < nomi.index("token"), \
        "il risultato deve precedere la spiegazione: tabella e grafico non aspettano la prosa"
    assert nomi[-1] == "done"


def test_il_risultato_arriva_senza_spiegazione(client, csv_bytes, monkeypatch):
    """La spiegazione viaggia nei `token`: duplicarla in `result` la mostrerebbe
    tutta insieme un istante prima, vanificando lo streaming."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _servizio_streaming(monkeypatch, Turn(
        question="totale?", code="x", explanation="non deve comparire qui",
        result=ExecutionSuccess(fig=None, value=700, summary="")))

    ev = dict(_eventi(client.post("/api/ask/stream",
                                  json={"dataset_id": d["dataset_id"],
                                        "question": "totale?"}).text))
    assert ev["result"]["ok"] is True
    assert ev["result"]["answer"] is None
    assert ev["result"]["value"] == 700


def test_lo_streaming_ricompone_il_testo_completo(client, csv_bytes, monkeypatch):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _servizio_streaming(monkeypatch, Turn(
        question="q", code="x", result=ExecutionSuccess(fig=None, value=1, summary="")))

    ev = _eventi(client.post("/api/ask/stream",
                             json={"dataset_id": d["dataset_id"], "question": "q"}).text)
    pezzi = [d_["text"] for n, d_ in ev if n == "token"]
    finale = [d_ for n, d_ in ev if n == "done"][0]
    assert "".join(pezzi) == "Il totale è 700."
    assert finale["answer"] == "Il totale è 700.", \
        "chi salva la conversazione non deve ricucire i pezzi"


def test_un_fallimento_chiude_lo_stream_senza_chiedere_una_narrazione(
        client, csv_bytes, monkeypatch):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    servizio = _servizio_streaming(monkeypatch, Turn(
        question="apri un file", code="open('/etc/passwd')",
        result=ExecutionFailure("security", "uso di 'open' non consentito")))

    ev = _eventi(client.post("/api/ask/stream",
                             json={"dataset_id": d["dataset_id"],
                                   "question": "apri un file"}).text)
    nomi = [n for n, _ in ev]
    assert "token" not in nomi, "non si chiede al modello di commentare un fallimento"
    assert dict(ev)["result"]["failure_kind"] == "security"
    assert nomi[-1] == "done"
    servizio.stream_explanation.assert_not_called()


def test_explain_false_non_paga_la_narrazione(client, csv_bytes, monkeypatch):
    """
    La rotta accettava `explain` e lo ignorava: chi chiedeva i soli numeri
    riceveva — e pagava — anche la seconda chiamata al modello. Sulla demo
    pubblica quella chiamata la paga il budget condiviso di tutti.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    servizio = _servizio_streaming(monkeypatch, Turn(
        question="totale?", code="x",
        result=ExecutionSuccess(fig=None, value=700, summary="700")))

    ev = _eventi(client.post("/api/ask/stream",
                             json={"dataset_id": d["dataset_id"], "question": "totale?",
                                   "explain": False}).text)
    nomi = [n for n, _ in ev]
    assert "result" in nomi, "i numeri si vogliono comunque"
    assert "token" not in nomi
    assert dict(ev)["done"]["answer"] is None
    servizio.stream_explanation.assert_not_called()


def test_lo_streaming_non_narra_cio_che_e_gia_scritto(client, csv_bytes, monkeypatch):
    """
    Terza strada che puo' narrare, e la piu' facile da dimenticare: qui la
    spiegazione non passa dal servizio, se la genera lo stream. Se il giudizio
    vivesse solo in `AnalysisService`, la demo React continuerebbe a pagare la
    narrazione mentre Streamlit non la paga piu'.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    servizio = _servizio_streaming(monkeypatch, Turn(
        question="qual e' il profitto?",
        code="# mappa: profitto -> NESSUNA\nresult = 'Non c'e' una colonna del profitto.'",
        result=ExecutionSuccess(fig=None, value="Non c'e' una colonna del profitto.",
                                summary="")))

    ev = _eventi(client.post("/api/ask/stream",
                             json={"dataset_id": d["dataset_id"],
                                   "question": "qual e' il profitto?"}).text)
    nomi = [n for n, _ in ev]
    assert "token" not in nomi
    assert dict(ev)["result"]["warnings"], "l'avviso deterministico resta: e' lui a informare"
    servizio.stream_explanation.assert_not_called()


def test_explain_resta_vero_per_difetto(client, csv_bytes, monkeypatch):
    """Il difetto opposto sarebbe peggiore: una chat muta senza che nessuno
    l'abbia chiesto. Chi non dice nulla continua a ricevere la spiegazione."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _servizio_streaming(monkeypatch, Turn(
        question="totale?", code="x",
        result=ExecutionSuccess(fig=None, value=700, summary="700")))

    ev = _eventi(client.post("/api/ask/stream",
                             json={"dataset_id": d["dataset_id"], "question": "totale?"}).text)
    assert "token" in [n for n, _ in ev]


def test_un_guasto_nel_servizio_diventa_un_evento_di_errore(client, csv_bytes, monkeypatch):
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    servizio = MagicMock()
    servizio.answer.side_effect = RuntimeError("il modello è esploso")
    monkeypatch.setattr("nlda.api.app.AnalysisService", lambda _a: servizio)
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: MagicMock())

    ev = _eventi(client.post("/api/ask/stream",
                             json={"dataset_id": d["dataset_id"], "question": "q"}).text)
    nomi = [n for n, _ in ev]
    assert "error" in nomi
    # Il dettaglio tecnico resta nei log: al client va un messaggio utile, non
    # il testo di un'eccezione interna.
    assert "esploso" not in dict(ev)["error"]["detail"]


def test_le_due_strade_producono_lo_STESSO_risultato(client, csv_bytes, monkeypatch):
    """
    `/ask` e `/ask/stream` costruiscono la risposta con la stessa funzione: se
    divergessero, un client che usa l'una si troverebbe campi diversi dall'altro.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    turn = Turn(question="q", code="risultato = df['Vendite'].sum()",
                result=ExecutionSuccess(fig=None, value=700, summary=""))
    _servizio_streaming(monkeypatch, turn)

    corpo = {"dataset_id": d["dataset_id"], "question": "q"}
    unica = client.post("/api/ask", json=corpo).json()
    a_pezzi = dict(_eventi(client.post("/api/ask/stream", json=corpo).text))["result"]

    ignora = {"answer"}   # nello streaming arriva dopo, nei token
    assert {k: v for k, v in unica.items() if k not in ignora} == \
           {k: v for k, v in a_pezzi.items() if k not in ignora}


# --- Le due interfacce non devono divergere -----------------------------------
def test_le_domande_d_esempio_arrivano_dal_backend(client, csv_bytes):
    """
    Erano scritte due volte, una per interfaccia, e avevano gia' divergato: la
    versione Streamlit proponeva fino a tre domande, quella React due e senza il
    ramo per il solo raggruppamento.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    assert d["example_questions"], "il client non deve inventarsele"
    assert any("Vendite" in q for q in d["example_questions"]), \
        "devono essere costruite sulle colonne di QUESTO dataset"

    from nlda.loader import (
        NamedBytesIO,
        date_columns,
        date_span_years,
        measure_columns,
        ordered_measures,
        read_any,
    )
    from nlda.suggestions import example_questions

    # Il confronto passa dalle STESSE informazioni che l'API ricava dal file:
    # colonna data e misure. Confrontare con la funzione chiamata a mani vuote
    # verificherebbe solo che due liste diverse sono diverse.
    df = read_any(NamedBytesIO(csv_bytes, "v.csv"))
    colonne_data = date_columns(df)
    assert d["example_questions"] == example_questions(
        "Vendite", "Regione",
        date_column=colonne_data[0] if colonne_data else None,
        date_span_years=date_span_years(df, colonne_data[0] if colonne_data else None),
        other_measures=ordered_measures(measure_columns(df)))


def test_le_domande_d_esempio_non_ripetono_il_report(client, csv_bytes):
    """
    Erano "Mostrami Vendite per Regione", "Quali sono i 5 Regione con piu'
    Vendite?" e "Qual e' il totale di Vendite?": le prime due sono il grafico
    della classifica, la terza e' il primo KPI in cima alla pagina. Chi le
    provava riceveva un numero che aveva gia' sotto gli occhi.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    domande = " | ".join(d["example_questions"]).lower()
    assert "totale di" not in domande, "il totale e' gia' il primo KPI"
    assert "mostrami vendite per regione" not in domande, "e' gia' il grafico della classifica"
    # Il dataset ha una colonna data: la domanda sul tempo dev'esserci.
    assert "in che" in domande


def test_config_espone_le_liste_che_il_client_ribatteva(client):
    j = client.get("/api/config").json()
    from nlda.suggestions import FREQUENCIES, PROJECT_QUESTIONS
    assert j["project_questions"] == list(PROJECT_QUESTIONS)
    assert j["frequencies"] == list(FREQUENCIES)


def test_il_report_dichiara_le_scelte_APPLICATE(client, csv_bytes):
    """
    Il client puo' lasciarle vuote, e allora le decide il backend sul dataset
    filtrato: un titolo costruito con lo stato del client nominerebbe una colonna
    diversa da quella tracciata.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    j = client.get(f"/api/dataset/{d['dataset_id']}/report").json()
    assert j["measure"] == "Vendite"
    assert j["category"] == "Regione"
    assert isinstance(j["unit"], str)


def test_l_etichetta_del_filtro_la_compone_il_backend(client, csv_bytes):
    """`views.apply_filter` distingue il valore singolo dall'insieme; il client
    ne scriveva una terza forma, sempre con '='."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    uno = client.get(f"/api/dataset/{d['dataset_id']}/report",
                     params={"filter_column": "Regione", "filter_values": ["Nord"]}).json()
    due = client.get(f"/api/dataset/{d['dataset_id']}/report",
                     params={"filter_column": "Regione",
                             "filter_values": ["Nord", "Sud"]}).json()
    assert uno["filter_label"] == "Regione = Nord"
    assert "∈" in due["filter_label"]
    # Senza filtro, nessuna etichetta da mostrare.
    assert client.get(f"/api/dataset/{d['dataset_id']}/report").json()["filter_label"] == ""


def test_la_mappa_di_correlazione_compare_quando_i_dati_la_giustificano(client):
    """Il modello dei dati la prometteva e l'API non la produceva mai, mentre
    Streamlit la disegna."""
    righe = b"".join(f"R{i % 4},{100 + i * 7},{40 + i * 3}\n".encode() for i in range(40))
    csv = b"Regione,Vendite,Costi\n" + righe
    d = client.post("/api/dataset", files={"file": ("due.csv", csv, "text/csv")}).json()
    assert len(d["measures"]) >= 2
    assert "corr" in client.get(f"/api/dataset/{d['dataset_id']}/report").json()["figures"]


def test_una_sola_misura_non_produce_correlazioni(client, csv_bytes):
    """La correlazione ha bisogno di almeno due misure: non si inventa un grafico."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    assert "corr" not in client.get(f"/api/dataset/{d['dataset_id']}/report").json()["figures"]


def test_il_consiglio_viaggia_con_il_fallimento(client, csv_bytes, monkeypatch):
    """La politica 'cosa dire quando fallisce' viveva solo nel client React."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    _finto_servizio(monkeypatch, Turn(
        question="apri un file", code="open('/etc/passwd')",
        result=ExecutionFailure("security", "uso di 'open' non consentito")))

    j = client.post("/api/ask", json={"dataset_id": d["dataset_id"],
                                      "question": "apri un file"}).json()
    from nlda.results import advice_for
    assert j["advice"] == advice_for("security")
    assert "sandbox" in j["advice"].lower()


def test_la_sintesi_ha_una_rotta_propria(client, csv_bytes, monkeypatch):
    """
    Il report React ne era privo mentre quello Streamlit ce l'ha. E' una rotta a
    parte perche' e' l'unica che aspetta il modello: dentro /report farebbe
    aspettare anche i numeri, che sono gia' pronti.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    agente = MagicMock()
    agente.overview.return_value = "Le vendite crescono da Nord a Sud."
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: agente)

    r = client.post(f"/api/dataset/{d['dataset_id']}/overview", params={"unit": "€"})
    assert r.status_code == 200
    assert r.json()["text"] == "Le vendite crescono da Nord a Sud."
    # L'unita' arriva al modello: senza, la inventerebbe.
    assert "€" in agente.overview.call_args.args[0]


def test_il_report_esecutivo_ha_una_rotta_propria(client, csv_bytes, monkeypatch):
    """L'ultima funzione che solo Streamlit aveva: cinque sezioni in Markdown."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    agente = MagicMock()
    agente.executive_report.return_value = "## Executive Summary\nCresce."
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: agente)

    r = client.post(f"/api/dataset/{d['dataset_id']}/executive-report", params={"unit": "€"})
    assert r.status_code == 200
    assert r.json()["markdown"].startswith("## Executive Summary")
    assert "€" in agente.executive_report.call_args.args[0]


def test_la_sintesi_e_il_report_partono_dagli_stessi_numeri(client, csv_bytes, monkeypatch):
    """
    Due prodotti diversi, un solo ingresso (`_testo_insight`): se divergessero,
    la pagina mostrerebbe una sintesi e un report che si contraddicono.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    agente = MagicMock()
    agente.overview.return_value = "sintesi"
    agente.executive_report.return_value = "## Executive Summary"
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: agente)

    parametri = {"unit": "€", "measure": "Vendite", "category": "Regione"}
    client.post(f"/api/dataset/{d['dataset_id']}/overview", params=parametri)
    client.post(f"/api/dataset/{d['dataset_id']}/executive-report", params=parametri)

    assert agente.overview.call_args.args[0] == agente.executive_report.call_args.args[0]


def test_senza_nulla_da_riassumere_le_due_rotte_si_comportano_diversamente(
        client, csv_bytes, monkeypatch):
    """
    La sintesi tace (`text: null`), il report esecutivo risponde 400.

    La sintesi arriva da sola e un silenzio si spiega; il report lo si e'
    CHIESTO, e un pulsante che non fa nulla sembrerebbe rotto.

    Il caso si costruisce svuotando `analyze`, non con un dataset senza numeri:
    su un dataset vero un testo c'e' sempre (almeno il conteggio delle righe),
    quindi un CSV di sole stringhe non basterebbe a raggiungere il ramo.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    monkeypatch.setattr("nlda.api.app.DataAgent", lambda **k: MagicMock())
    monkeypatch.setattr("nlda.api.app.analyze", lambda *a, **k: {})

    assert client.post(f"/api/dataset/{d['dataset_id']}/overview").json()["text"] is None

    r = client.post(f"/api/dataset/{d['dataset_id']}/executive-report")
    assert r.status_code == 400
    assert "riassumere" in r.json()["detail"]


def _categorie_del_grafico(figura) -> list:
    """
    Le etichette di una figura a barre, comunque sia orientata.

    `charts.to_chart` sceglie l'orientamento in base alla lunghezza e al numero
    delle etichette: orizzontale mette le categorie su `y`, verticale su `x`. Un
    test che ne desse per scontato uno si romperebbe rinominando una colonna.
    """
    traccia = figura["data"][0]
    return list(traccia["y"] if traccia.get("orientation") == "h" else traccia["x"])


def test_il_filtro_non_riduce_la_classifica_a_una_barra(client):
    """
    Cliccando una barra la pagina si filtra su quella categoria — e il grafico
    della classifica restava con UNA barra sola, larga quanto il pannello. Una
    classifica di un elemento non e' una classifica: il confronto con le altre e'
    l'informazione, e filtrandolo si perde.

    Ora quel grafico si calcola sui dati NON filtrati e la selezione si evidenzia
    con l'opacita'.
    """
    righe = b"".join(f"R{i % 4},{100 + i * 7}\n".encode() for i in range(40))
    d = client.post("/api/dataset",
                    files={"file": ("r.csv", b"Regione,Vendite\n" + righe, "text/csv")}).json()

    intere = _categorie_del_grafico(
        client.get(f"/api/dataset/{d['dataset_id']}/report").json()["figures"]["top"])
    assert len(intere) == 4, "il dataset ha quattro regioni"

    filtrato = client.get(f"/api/dataset/{d['dataset_id']}/report",
                          params={"filter_column": "Regione", "filter_values": ["R0"]}).json()
    top = filtrato["figures"]["top"]
    assert _categorie_del_grafico(top) == intere, "la classifica conserva tutte le categorie"

    # La selezione si distingue: piena la scelta, attenuate le altre.
    opacita = top["data"][0]["marker"]["opacity"]
    assert sorted(opacita) == [0.32, 0.32, 0.32, 1.0]
    scelta = _categorie_del_grafico(top)[list(opacita).index(1.0)]
    assert scelta == "R0", "a essere piena dev'essere la barra cliccata"


def test_senza_filtro_le_barre_sono_tutte_piene(client, csv_bytes):
    """L'evidenziazione compare solo quando c'e' una selezione da evidenziare."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    top = client.get(f"/api/dataset/{d['dataset_id']}/report").json()["figures"]["top"]
    assert "opacity" not in top["data"][0].get("marker", {})


def test_un_filtro_su_un_ALTRA_colonna_restringe_la_classifica(client):
    """
    L'eccezione vale solo per la categoria che il grafico disegna. Filtrando su
    una colonna diversa la classifica DEVE restringersi: e' la domanda che si e'
    fatta ("le vendite per regione, ma solo per il segmento X").

    Il dataset e' costruito perche' il segmento S0 esista solo in due regioni su
    quattro: cosi' la differenza si vede nelle ETICHETTE, senza dover decodificare
    gli array numerici che Plotly serializza in base64.
    """
    righe = b"".join(
        f"R{i % 4},{'S0' if i % 4 < 2 else 'S1'},{100 + i * 7}\n".encode() for i in range(40))
    d = client.post("/api/dataset",
                    files={"file": ("s.csv", b"Regione,Segmento,Vendite\n" + righe,
                                    "text/csv")}).json()

    intero = client.get(f"/api/dataset/{d['dataset_id']}/report",
                        params={"category": "Regione"}).json()
    filtrato = client.get(f"/api/dataset/{d['dataset_id']}/report",
                          params={"category": "Regione", "filter_column": "Segmento",
                                  "filter_values": ["S0"]}).json()

    assert sorted(_categorie_del_grafico(intero["figures"]["top"])) == ["R0", "R1", "R2", "R3"]
    assert sorted(_categorie_del_grafico(filtrato["figures"]["top"])) == ["R0", "R1"]
    # E nessuna evidenziazione: non c'e' una categoria selezionata da illuminare.
    assert "opacity" not in filtrato["figures"]["top"]["data"][0].get("marker", {})


# --- I report gia' calcolati non si rifanno -----------------------------------
def test_lo_stesso_report_non_si_ricalcola(client, csv_bytes, monkeypatch):
    """
    Cliccare una barra filtra la pagina, ricliccarla toglie il filtro: a quel
    punto serve ESATTAMENTE il report di un secondo prima. Sulla demo quel
    ritorno costava 1,5 secondi di attesa e di CPU su un container che di CPU ne
    ha un decimo. Si conta il numero di CALCOLI, non la latenza: e' il lavoro
    risparmiato la cosa da difendere.
    """
    import nlda.api.app as modulo

    calcoli = []
    vero = modulo.analyze
    monkeypatch.setattr(modulo, "analyze",
                        lambda *a, **k: (calcoli.append(1), vero(*a, **k))[1])

    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    primo = client.get(f"/api/dataset/{d['dataset_id']}/report")
    secondo = client.get(f"/api/dataset/{d['dataset_id']}/report")

    assert primo.json() == secondo.json()
    assert len(calcoli) == 1, "il secondo report e' stato ricalcolato"


def test_parametri_diversi_non_condividono_il_ricordo(client, csv_bytes):
    """
    E' il difetto peggiore che una cache possa avere: mostrare i numeri
    dell'intero dataset dicendo che sono quelli filtrati. La chiave deve
    contenere TUTTO cio' da cui la risposta dipende.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    did = d["dataset_id"]
    intero = client.get(f"/api/dataset/{did}/report").json()
    filtrato = client.get(f"/api/dataset/{did}/report",
                          params={"filter_column": "Regione", "filter_values": "Nord"}).json()

    assert intero["filter_label"] == ""
    assert filtrato["filter_label"]
    assert intero["kpis"][0]["value"] != filtrato["kpis"][0]["value"]


def test_un_dataset_scaduto_da_404_anche_se_il_report_e_ricordato(client, csv_bytes):
    """
    Il ricordo non deve tenere in vita un dataset che il magazzino ha lasciato
    andare: la pagina resterebbe consultabile mentre le domande falliscono, e
    l'utente non capirebbe perche'. Il controllo del dataset viene PRIMA.
    """
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    did = d["dataset_id"]
    assert client.get(f"/api/dataset/{did}/report").status_code == 200

    store.magazzino.svuota()          # scaduto o sfrattato
    assert client.get(f"/api/dataset/{did}/report").status_code == 404


def test_un_errore_non_diventa_permanente(client, csv_bytes):
    """Se si ricordassero anche i fallimenti, un guasto momentaneo resterebbe
    per tutti finche' la voce non esce dalla cache."""
    d = client.post("/api/dataset", files={"file": ("v.csv", csv_bytes, "text/csv")}).json()
    did = d["dataset_id"]
    assert client.get(f"/api/dataset/{did}/report",
                      params={"measure": "Regione"}).status_code == 400
    assert client.get(f"/api/dataset/{did}/report",
                      params={"measure": "Regione"}).status_code == 400
    assert len(cache.ricordi) == 0, "un errore e' finito fra i ricordi"


def test_i_ricordi_contano_i_BYTE_non_le_voci():
    """
    Contare le voci e' contare le tabelle un'altra volta — l'errore che il
    magazzino ha gia' fatto e gia' pagato. Una risposta di report non pesa
    sempre uguale: su 300.000 righe ne pesa 3,3 MB, perche' la figura della
    distribuzione si porta dentro ogni valore della colonna misura. Trenta voci
    cosi' sarebbero 96 MB su un container da 512.
    """
    class Risposta:
        def __init__(self, mega: float):
            self._testo = "x" * int(mega * 1024 * 1024)

        def model_dump_json(self) -> str:
            return self._testo

    ricordi = cache.Ricordi(ram_mb=4)
    for i in range(6):
        ricordi.ottieni(f"k{i}", lambda i=i: Risposta(1.5))

    assert ricordi.byte_totali() <= 4 * 1024 * 1024
    assert len(ricordi) < 6, "con un tetto a byte le voci non possono restare tutte"


def test_una_risposta_piu_grande_del_tetto_si_tiene_e_lo_si_dichiara(caplog):
    """Come il magazzino: buttarla non aiuterebbe chi l'ha appena chiesta, ma un
    tetto tarato male si deve poter vedere nei log."""
    class Enorme:
        def model_dump_json(self) -> str:
            return "x" * (5 * 1024 * 1024)

    ricordi = cache.Ricordi(ram_mb=1)
    registro = logging.getLogger("nlda.api.cache")
    with caplog.at_level(logging.WARNING, logger="nlda.api.cache"):
        registro.addHandler(caplog.handler)   # il logger del progetto non propaga
        try:
            ricordi.ottieni("grande", Enorme)
        finally:
            registro.removeHandler(caplog.handler)

    assert len(ricordi) == 1
    assert "cache_voce_oltre_il_tetto" in caplog.text
