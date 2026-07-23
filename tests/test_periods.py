"""
Confronto tra periodi. Si testa il calcolo (aggregazione per periodo e variazione
percentuale) e il fatto che la funzione sia DAVVERO esposta al codice della sandbox
— un helper insegnato nel prompt ma non iniettato darebbe un NameError all'utente.
"""
import math

import pandas as pd
import pytest

from nlda.config import Settings
from nlda.periods import compare_periods
from nlda.results import ExecutionSuccess
from nlda.sandbox.runner import execute_pandas_code


@pytest.fixture
def vendite() -> pd.DataFrame:
    return pd.DataFrame({
        "data": pd.to_datetime(["2023-01-15", "2023-02-20", "2023-04-10", "2023-07-05"]),
        "val": [10, 20, 30, 40],
    })


# --- Calcolo -------------------------------------------------------------------
def test_aggrega_per_trimestre_e_calcola_la_variazione(vendite):
    out = compare_periods(vendite, "data", "val", freq="trimestre")
    assert list(out["periodo"]) == ["2023Q1", "2023Q2", "2023Q3"]
    assert list(out["val"]) == [30, 30, 40]              # Q1: 10+20, Q2: 30, Q3: 40
    assert math.isnan(out["variazione_%"].iloc[0])       # il primo non ha un prima
    assert out["variazione_%"].iloc[1] == 0.0            # 30 → 30
    assert out["variazione_%"].iloc[2] == pytest.approx(33.3)  # 30 → 40


def test_frequenza_mese_e_anno(vendite):
    mesi = compare_periods(vendite, "data", "val", freq="mese")
    assert list(mesi["periodo"]) == ["2023-01", "2023-02", "2023-04", "2023-07"]

    anni = compare_periods(vendite, "data", "val", freq="anno")
    assert list(anni["periodo"]) == ["2023"]
    assert list(anni["val"]) == [100]


def test_aggregazione_personalizzata(vendite):
    out = compare_periods(vendite, "data", "val", freq="anno", agg="mean")
    assert out["val"].iloc[0] == pytest.approx(25.0)     # media di 10,20,30,40


# --- Errori chiari -------------------------------------------------------------
def test_frequenza_non_valida_solleva(vendite):
    with pytest.raises(ValueError, match="Frequenza"):
        compare_periods(vendite, "data", "val", freq="settimana")


def test_colonna_senza_date_solleva():
    df = pd.DataFrame({"data": ["non", "una", "data"], "val": [1, 2, 3]})
    with pytest.raises(ValueError, match="date"):
        compare_periods(df, "data", "val")


# --- Integrazione: la funzione è esposta al codice della sandbox ---------------
def test_compare_periods_e_chiamabile_dentro_la_sandbox(monkeypatch, vendite):
    # In-process (l'iniezione vive in _run_code, condiviso coi due percorsi): un
    # helper insegnato ma non iniettato darebbe qui un fallimento 'runtime'.
    monkeypatch.setattr("nlda.sandbox.runner.settings", Settings(sandbox_subprocess=False))
    esito = execute_pandas_code(
        "result = compare_periods(df, 'data', 'val', freq='trimestre')", vendite)

    assert isinstance(esito, ExecutionSuccess)
    assert list(esito.value["periodo"]) == ["2023Q1", "2023Q2", "2023Q3"]
