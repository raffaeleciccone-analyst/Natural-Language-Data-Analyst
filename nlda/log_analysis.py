"""
Riepilogo dei log strutturati (quelli emessi con `LOG_FORMAT=json`).

Chiude il cerchio dell'osservabilità: non basta *loggare* costo, latenza ed esito
per turno — i log vanno *letti*. Qui si aggregano in poche metriche azionabili
(tasso di successo, costo, percentili di latenza, esiti per causa), quelle su cui
si decide se un provider conviene o se qualcosa sta peggiorando.

Funzioni pure, nessun I/O: `scripts/analyze_logs.py` è il sottile wrapper da riga
di comando (legge un file o stdin), così l'aggregazione si testa da sola.
"""
import json
from collections import Counter


def parse_lines(lines) -> list[dict]:
    """
    Estrae i record JSON, saltando in silenzio le righe non-JSON: un file di log
    può mescolare formato testo e JSON, o contenere righe di altri strumenti.
    """
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _percentiles(valori: list) -> dict:
    """p50/p95/max su una lista già ordinata (metodo del rango più vicino)."""
    if not valori:
        return {}
    def al(q: float):
        return valori[min(len(valori) - 1, int(q * len(valori)))]
    return {"p50": al(0.50), "p95": al(0.95), "max": valori[-1]}


def _num(records: list[dict], event: str, field: str) -> list:
    """Valori numerici di un campo per un dato evento (ignora i mancanti/non numerici)."""
    return [r[field] for r in records
            if r.get("event") == event and isinstance(r.get(field), (int, float))]


def summarize(records: list[dict]) -> dict:
    """Aggrega i record (per evento) in un riepilogo di metriche."""
    turn_ends = [r for r in records if r.get("event") == "turn_end"]
    calls_ok = [r for r in records if r.get("event") == "provider_call_ok"]
    calls_err = [r for r in records if r.get("event") == "provider_call_error"]

    esiti = Counter(r["outcome"] for r in turn_ends if r.get("outcome"))
    costo = sum(_num(records, "provider_call_ok", "cost_usd"))
    latenze = sorted(_num(records, "turn_end", "latency_ms"))

    n_turni = len(turn_ends)
    n_ok = esiti.get("ok", 0)
    return {
        "turni": n_turni,
        "successi": n_ok,
        "tasso_successo": (n_ok / n_turni) if n_turni else None,
        "esiti": dict(esiti),
        "costo_usd_totale": round(costo, 6),
        "costo_usd_medio_turno": round(costo / n_turni, 6) if n_turni else None,
        "token_input": sum(r.get("input_tokens") or 0 for r in calls_ok),
        "token_output": sum(r.get("output_tokens") or 0 for r in calls_ok),
        "latenza_turno_ms": _percentiles(latenze),
        "chiamate_provider": len(calls_ok) + len(calls_err),
        "chiamate_in_errore": len(calls_err),
    }


def format_summary(s: dict) -> str:
    """Riepilogo leggibile per il terminale."""
    if not s["turni"]:
        return "Nessun turno nei log (servono log in formato JSON: LOG_FORMAT=json)."

    tasso = f"{s['tasso_successo']:.0%}" if s["tasso_successo"] is not None else "—"
    esiti = ", ".join(f"{k}={v}" for k, v in sorted(s["esiti"].items())) or "—"
    lat = s["latenza_turno_ms"]
    lat_txt = (f"p50 {lat.get('p50')} ms · p95 {lat.get('p95')} ms · max {lat.get('max')} ms"
               if lat else "—")
    return "\n".join([
        f"Riepilogo dei log — {s['turni']} turni",
        f"  Successi:       {s['successi']}/{s['turni']} ({tasso})",
        f"  Esiti:          {esiti}",
        f"  Costo:          ${s['costo_usd_totale']}  (media ${s['costo_usd_medio_turno']}/turno)",
        f"  Token:          input {s['token_input']} · output {s['token_output']}",
        f"  Latenza turno:  {lat_txt}",
        f"  Chiamate LLM:   {s['chiamate_provider']}  (in errore: {s['chiamate_in_errore']})",
    ])
