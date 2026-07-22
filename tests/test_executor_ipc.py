"""
Test del canale di comunicazione con il sottoprocesso della sandbox.

Il worker esegue codice generato da un LLM: va trattato come processo
potenzialmente compromesso. Il canale di RITORNO (worker -> genitore) deve quindi
trasportare solo dati inerti (JSON) e mai oggetti serializzati con pickle, che il
genitore ricostruirebbe eseguendo codice — annullando la barriera di processo.

Questi test valgono come regression di sicurezza sull'invariante:
    "tutto ciò che il worker restituisce dev'essere JSON-serializzabile".
"""
import json

import numpy as np
import pandas as pd
import pytest

from core.executor import (
    _decode_value,
    _deserialize_result,
    _encode_value,
    execute_pandas_code,
    serialize_result,
)
from core.results import ExecutionFailure, ExecutionSuccess


# --- Invariante di sicurezza ---------------------------------------------------
@pytest.mark.parametrize("value", [
    None,
    42,
    3.14,
    "testo",
    True,
    np.int64(7),                                   # scalari numpy
    np.float64(2.5),
    pd.Series([1, 2, 3], name="valori"),
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}),
    pd.Timestamp("2023-01-05"),
    object(),                                      # tipo arbitrario -> degrada a testo
])
def test_il_risultato_e_sempre_json_serializzabile(value):
    payload = serialize_result(ExecutionSuccess(fig=None, value=value, summary="s"))
    # Se questo solleva, il canale sta trasportando qualcosa di non inerte.
    json.dumps(payload)


def test_il_fallimento_resta_json_e_conserva_la_causa():
    payload = serialize_result(ExecutionFailure("security", "Errore di sicurezza: qualcosa", "df.x"))
    assert payload["kind"] == "err"
    json.dumps(payload)
    # La causa deve sopravvivere al viaggio: è ciò che decide se ritentare.
    out = _deserialize_result(payload)
    assert isinstance(out, ExecutionFailure)
    assert out.kind == "security"
    assert out.retryable is False
    assert out.code == "df.x"


def test_causa_sconosciuta_dal_worker_degrada_a_internal():
    # Il worker esegue codice non fidato: un 'kind' inatteso non deve diventare
    # una causa arbitraria (né, peggio, una causa ritentabile scelta da lui).
    out = _deserialize_result({"kind": "err", "failure": {"kind": "inventata", "message": "x"}})
    assert isinstance(out, ExecutionFailure)
    assert out.kind == "internal"
    assert out.retryable is False


# --- Round-trip: il valore sopravvive al viaggio -------------------------------
def test_roundtrip_dataframe():
    df = pd.DataFrame({"cat": ["a", "b"], "val": [10.5, 20.25]})
    out = _decode_value(_encode_value(df))
    assert isinstance(out, pd.DataFrame)
    pd.testing.assert_frame_equal(out, df, check_dtype=False)


def test_roundtrip_series_conserva_il_nome():
    s = pd.Series([1.0, 2.0], name="valori")
    out = _decode_value(_encode_value(s))
    assert isinstance(out, pd.Series)
    assert out.name == "valori"
    assert list(out) == [1.0, 2.0]


@pytest.mark.parametrize("value,atteso", [(42, 42), (2.5, 2.5), ("x", "x"), (True, True)])
def test_roundtrip_scalari(value, atteso):
    assert _decode_value(_encode_value(value)) == atteso


def test_scalare_numpy_diventa_tipo_python():
    out = _decode_value(_encode_value(np.float64(2.5)))
    assert out == 2.5
    assert type(out) is float  # non un tipo numpy: il JSON non li trasporta


def test_oggetto_non_serializzabile_degrada_a_testo():
    payload = _encode_value(object())
    assert payload["kind"] == "text"
    assert isinstance(_decode_value(payload), str)


def test_none_resta_none():
    assert _decode_value(_encode_value(None)) is None


# --- Robustezza: payload malformato non deve rompere l'app ---------------------
@pytest.mark.parametrize("payload", [
    None,
    "non un dict",
    b"\x80\x04\x95",          # byte di un pickle: devono essere inerti, non eseguiti
])
def test_payload_non_valido_produce_errore_leggibile(payload):
    out = _deserialize_result(payload)
    assert isinstance(out, ExecutionFailure)
    assert out.kind == "internal"
    assert out.message.startswith("Errore")


def test_frame_malformato_degrada_senza_sollevare():
    assert _decode_value({"kind": "frame", "data": "{ non json"}) is None


def test_kind_sconosciuto_non_solleva():
    assert _decode_value({"kind": "qualcosa-di-nuovo"}) is None


# --- End-to-end attraverso il sottoprocesso reale ------------------------------
# Copre il percorso completo: pickle in andata, esecuzione isolata, JSON al ritorno.
def test_esecuzione_nel_sottoprocesso_reale(sales_df: pd.DataFrame):
    res = execute_pandas_code("df['Sales'].sum()", sales_df)
    assert isinstance(res, ExecutionSuccess)
    assert res.value == sales_df["Sales"].sum()


def test_sottoprocesso_reale_con_dataframe(sales_df: pd.DataFrame):
    res = execute_pandas_code(
        "risultato = df.groupby('Region', as_index=False)['Sales'].sum()", sales_df)
    assert isinstance(res, ExecutionSuccess)
    assert isinstance(res.value, pd.DataFrame)
    assert set(res.value["Region"]) == {"North", "South", "West"}


def test_sottoprocesso_reale_propaga_la_causa_dell_errore(sales_df: pd.DataFrame):
    # L'errore nasce NEL worker: la causa deve arrivare al padre attraverso il JSON.
    out = execute_pandas_code("df['ColonnaInesistente'].sum()", sales_df)
    assert isinstance(out, ExecutionFailure)
    assert out.kind == "runtime"


def test_sottoprocesso_reale_blocca_codice_pericoloso(sales_df: pd.DataFrame):
    out = execute_pandas_code("df.to_csv('x.csv')", sales_df)
    assert isinstance(out, ExecutionFailure)
    assert out.kind == "security"
    assert "sicurezza" in out.message.lower()
