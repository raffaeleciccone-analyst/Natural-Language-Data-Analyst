"""
Aggregazione dei log JSON. Funzioni pure: si verifica che i record vengano
sommati nelle metriche giuste e che le righe non-JSON non facciano esplodere nulla.
"""
import json

from nlda.log_analysis import format_summary, parse_lines, summarize

_RECORDS = [
    {"event": "turn_start", "turn_id": "a"},
    {"event": "provider_call_ok", "cost_usd": 0.0003, "input_tokens": 100, "output_tokens": 50},
    {"event": "turn_end", "outcome": "ok", "latency_ms": 900},
    {"event": "turn_start", "turn_id": "b"},
    {"event": "provider_call_ok", "cost_usd": 0.0001, "input_tokens": 80, "output_tokens": 20},
    {"event": "turn_end", "outcome": "runtime", "latency_ms": 1500},
    {"event": "provider_call_error", "retryable": True},
]


# --- parse_lines ---------------------------------------------------------------
def test_parse_salta_le_righe_non_json():
    righe = [json.dumps(_RECORDS[0]), "", "una riga di testo non-JSON",
             "12:00:03 [INFO] nlda: qualcosa", json.dumps(_RECORDS[1])]
    parsed = parse_lines(righe)
    assert len(parsed) == 2
    assert parsed[0]["event"] == "turn_start"


def test_parse_ignora_json_non_oggetto():
    assert parse_lines(["[1, 2, 3]", "42", '"stringa"']) == []


# --- summarize -----------------------------------------------------------------
def test_metriche_aggregate():
    s = summarize(_RECORDS)
    assert s["turni"] == 2
    assert s["successi"] == 1
    assert s["tasso_successo"] == 0.5
    assert s["esiti"] == {"ok": 1, "runtime": 1}
    assert s["costo_usd_totale"] == 0.0004
    assert s["costo_usd_medio_turno"] == 0.0002
    assert s["token_input"] == 180 and s["token_output"] == 70
    assert s["latenza_turno_ms"]["max"] == 1500
    assert s["chiamate_provider"] == 3      # 2 ok + 1 errore
    assert s["chiamate_in_errore"] == 1


def test_log_vuoti_non_esplodono():
    s = summarize([])
    assert s["turni"] == 0
    assert s["tasso_successo"] is None
    assert s["latenza_turno_ms"] == {}


# --- format_summary ------------------------------------------------------------
def test_report_leggibile_contiene_le_cifre():
    testo = format_summary(summarize(_RECORDS))
    assert "2 turni" in testo
    assert "50%" in testo                    # tasso di successo
    assert "0.0004" in testo                 # costo totale


def test_report_su_log_vuoti_lo_dichiara():
    testo = format_summary(summarize([]))
    assert "Nessun turno" in testo and "LOG_FORMAT=json" in testo
