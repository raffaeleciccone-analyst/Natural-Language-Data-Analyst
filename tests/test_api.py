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
from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from nlda.api import store
from nlda.api.app import app
from nlda.results import ExecutionFailure, ExecutionSuccess
from nlda.service import Turn


@pytest.fixture
def client():
    store.magazzino.svuota()
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


def test_lo_schema_openapi_si_genera(client):
    """Se lo schema non si genera, il frontend non puo' derivarne i tipi."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    percorsi = r.json()["paths"]
    for atteso in ["/api/config", "/api/dataset", "/api/ask", "/api/project-qa"]:
        assert atteso in percorsi


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


def test_report_di_un_dataset_inesistente_da_404(client):
    r = client.get("/api/dataset/inventato/report")
    assert r.status_code == 404
    assert "ricaricalo" in r.json()["detail"]


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
