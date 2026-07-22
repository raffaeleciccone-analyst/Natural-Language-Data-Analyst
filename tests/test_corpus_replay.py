"""
Rigioca offline risposte REALI del modello attraverso l'intera pipeline.

Il resto della suite sostituisce il provider con un finto che risponde ciò che il
test decide: utile per l'orchestrazione, inutile per scoprire come la pipeline
tratta ciò che un modello produce davvero. È lì che si è già nascosta una
rottura — una guardia aggiunta a `_wrap_chart` aveva smesso di produrre grafici
proprio per la forma di codice più frequente, con la suite verde.

Il corpus (`tests/fixtures/model_outputs.json`) contiene l'output GREZZO del
modello per una dozzina di domande, registrato una volta con
`scripts/record_corpus.py`. Qui viene rigiocato in modo deterministico e senza
rete.

Due tipi di asserzione, tenuti separati di proposito:

* sul NOSTRO codice, severe: qualunque cosa il modello abbia risposto,
  l'avvolgimento deve produrre Python valido che supera la sandbox, e una
  risposta tabellare a una domanda grafica deve avere la sua figura. Queste
  valgono sempre, indipendentemente dalla bravura del modello;
* sul MODELLO, una soglia: si misura quante risposte sono eseguibili e si
  pretende che non peggiori. Un modello sbaglia, e va bene: quello che non deve
  succedere è che peggiori la pipeline senza che nessuno se ne accorga.
"""
import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from nlda.agent import DataAgent
from nlda.config import Settings
from nlda.loader import load_dataset
from nlda.results import ExecutionFailure, ExecutionSuccess
from nlda.sandbox.runner import execute_pandas_code
from nlda.sandbox.validator import _parse_and_validate

CORPUS = Path(__file__).parent / "fixtures" / "model_outputs.json"

# Quante risposte del corpus devono restare eseguibili. Sotto il valore misurato
# al momento della registrazione: serve a segnalare una REGRESSIONE della
# pipeline, non a certificare la qualità del modello.
SOGLIA_ESEGUIBILI = 9


@pytest.fixture(scope="module")
def casi() -> list[dict]:
    assert CORPUS.exists(), (
        "corpus mancante: registralo con `python scripts/record_corpus.py`")
    dati = json.loads(CORPUS.read_text(encoding="utf8"))
    assert dati["cases"], "corpus vuoto"
    return dati["cases"]


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    # Lo stesso dataset con cui il corpus è stato registrato: il codice del
    # modello cita colonne concrete e non gira su un DataFrame qualsiasi.
    return load_dataset()


@pytest.fixture(autouse=True)
def sandbox_in_process(monkeypatch):
    """In-process: qui si testa la pipeline, non l'isolamento (già coperto altrove)."""
    monkeypatch.setattr("nlda.sandbox.runner.settings", Settings(sandbox_subprocess=False))


def _agent() -> DataAgent:
    return DataAgent(provider="ollama")  # nessuna rete: si usano solo i metodi puri


def _wrap(caso: dict) -> str:
    return _agent()._wrap_chart(caso["raw_code"], caso["wants_chart"], caso["kind"])


# --- Invarianti sul NOSTRO codice: valgono sempre ------------------------------
def test_lavvolgimento_produce_sempre_python_valido(casi: list[dict]):
    """
    Qualunque cosa il modello risponda, `_wrap_chart` non deve mai produrre codice
    non parsificabile. È la classe di bug che ha già colpito: inserendo del codice
    con assegnazione dentro una chiamata si otteneva `to_chart(result = df...)`,
    sintassi valida ma semanticamente un argomento keyword.
    """
    for caso in casi:
        code = _wrap(caso)
        try:
            ast.parse(code)
        except SyntaxError as e:
            pytest.fail(f"{caso['question']!r}: avvolgimento non parsificabile ({e.msg})\n{code}")


def test_lavvolgimento_non_produce_mai_codice_che_rifiutiamo(casi: list[dict]):
    """Se l'avvolgimento producesse codice bloccato dalla sandbox, il fallimento
    sarebbe colpa nostra e non del modello — e non sarebbe correggibile."""
    for caso in casi:
        esito = _parse_and_validate(_wrap(caso))
        if isinstance(esito, ExecutionFailure) and esito.kind == "security":
            pytest.fail(f"{caso['question']!r}: avvolgimento rifiutato dalla sandbox "
                        f"-> {esito.message}")


def test_una_risposta_tabellare_a_domanda_grafica_ha_la_figura(casi: list[dict], df):
    """
    Il contratto che l'app promette: se la domanda chiede un grafico e i dati sono
    graficabili, il grafico ci deve essere. È la regressione che la suite verde non
    aveva colto quando "Mostrami le vendite per regione" smise di produrlo.
    """
    verificati = 0
    for caso in casi:
        if not caso["wants_chart"]:
            continue
        res = execute_pandas_code(_wrap(caso), df)
        if not isinstance(res, ExecutionSuccess):
            continue  # il modello ha sbagliato: non è questo il test che lo giudica
        if isinstance(res.value, (pd.DataFrame, pd.Series)) and len(res.value) > 1:
            assert res.fig is not None, (
                f"{caso['question']!r}: dati graficabili ma nessuna figura\n{_wrap(caso)}")
            verificati += 1
    assert verificati >= 3, f"solo {verificati} casi grafici verificati: corpus da arricchire"


def test_i_dati_arrivano_sempre_allutente(casi: list[dict], df):
    """Anche quando la figura non si può disegnare, il risultato non deve sparire."""
    for caso in casi:
        res = execute_pandas_code(_wrap(caso), df)
        if isinstance(res, ExecutionSuccess):
            assert res.fig is not None or res.value is not None, (
                f"{caso['question']!r}: né figura né dati")


# --- Misura sul MODELLO: una soglia, non un assoluto ---------------------------
def test_la_quota_di_risposte_eseguibili_non_peggiora(casi: list[dict], df):
    esiti = {c["question"]: execute_pandas_code(_wrap(c), df) for c in casi}
    ok = [q for q, r in esiti.items() if isinstance(r, ExecutionSuccess)]
    falliti = {q: r.message[:70] for q, r in esiti.items() if isinstance(r, ExecutionFailure)}

    assert len(ok) >= SOGLIA_ESEGUIBILI, (
        f"eseguibili {len(ok)}/{len(casi)}, sotto la soglia {SOGLIA_ESEGUIBILI}. "
        f"Falliti: {falliti}")
