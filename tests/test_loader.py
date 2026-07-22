"""Test del loader: rilevamento date, colonne-misura vs identificatori, lettura file, analyze."""
import io

import pandas as pd
import pytest

from nlda.loader import (
    _maybe_parse_dates,
    analyze,
    measure_columns,
    monthly_trend,
    read_any,
)


def test_rileva_colonne_data_per_nome():
    df = pd.DataFrame({"data": ["2023-01-01", "2023-02-01", "2023-03-01"]})
    out = _maybe_parse_dates(df.copy())
    assert pd.api.types.is_datetime64_any_dtype(out["data"])


def test_non_confonde_id_con_date():
    # colonna 'codice' numerica: non deve diventare una data
    df = pd.DataFrame({"codice": ["10010", "20020", "30030"]})
    out = _maybe_parse_dates(df.copy())
    assert not pd.api.types.is_datetime64_any_dtype(out["codice"])


def test_measure_columns_esclude_identificatori():
    # Sales con valori ripetuti (una misura reale non è quasi-tutta distinta),
    # Customer ID sequenziale (chiave), Year costante (dimensione temporale).
    df = pd.DataFrame({
        "Customer ID": range(1, 31),
        "Sales": [100.0, 200.0, 150.0] * 10,
        "Year": [2020] * 30,
    })
    measures = measure_columns(df)
    assert "Sales" in measures
    assert "Customer ID" not in measures  # ID escluso (nome + quasi-chiave)
    assert "Year" not in measures          # anno escluso


def test_read_any_csv_da_bytes():
    csv = b"Region,Sales\nNorth,100\nSouth,200\n"
    buf = io.BytesIO(csv)
    buf.name = "vendite.csv"  # type: ignore[attr-defined]
    df = read_any(buf)
    assert list(df.columns) == ["Region", "Sales"]
    assert len(df) == 2


def test_monthly_trend_aggrega_per_mese(sales_df: pd.DataFrame):
    per = monthly_trend(sales_df, "Order Date", "Sales")
    assert per is not None
    assert len(per) == 3  # gennaio, febbraio, marzo
    assert per["Sales"].sum() == sales_df["Sales"].sum()


def test_analyze_produce_insight(sales_df: pd.DataFrame):
    res = analyze(sales_df, measure="Sales", category="Region")
    assert res["measure"] == "Sales"
    assert res["category"] == "Region"
    assert "top" in res and "trend" in res
    assert isinstance(res["text"], str) and res["text"]


# --- read_any: gli altri formati accettati dall'upload -------------------------
def _upload(contenuto: bytes, nome: str) -> io.BytesIO:
    """Imita il file caricato da Streamlit: un buffer di byte con un attributo .name."""
    buf = io.BytesIO(contenuto)
    buf.name = nome  # type: ignore[attr-defined]
    return buf


def test_read_any_excel():
    # L'estensione .xlsx è offerta nella UI: senza questo test la lettura Excel
    # (e la dipendenza openpyxl che la rende possibile) non è verificata da nulla.
    origine = pd.DataFrame({"Region": ["North", "South"], "Sales": [100, 200]})
    out = io.BytesIO()
    origine.to_excel(out, index=False, engine="openpyxl")
    out.seek(0)

    df = read_any(_upload(out.getvalue(), "vendite.xlsx"))
    assert list(df.columns) == ["Region", "Sales"]
    assert df["Sales"].sum() == 300


def test_read_any_json_lista_di_record():
    payload = b'[{"Region": "North", "Sales": 100}, {"Region": "South", "Sales": 200}]'
    df = read_any(_upload(payload, "vendite.json"))
    assert list(df.columns) == ["Region", "Sales"]
    assert len(df) == 2


def test_read_any_json_annidato_diventa_stringa():
    # Liste e dizionari dentro una cella romperebbero groupby, grafici e tabelle:
    # vengono serializzati in stringa (colonne appiattite dove possibile).
    payload = b'[{"Region": "North", "Tags": ["a", "b"], "Info": {"code": 1}}]'
    df = read_any(_upload(payload, "annidato.json"))
    assert df.loc[0, "Tags"] == '["a", "b"]'
    # json_normalize appiattisce i dizionari in colonne separate.
    assert "Info.code" in df.columns
    # Nessuna cella resta di tipo lista/dict.
    assert not df.map(lambda v: isinstance(v, (list, dict))).any().any()


def test_read_any_json_scalare_e_rifiutato():
    # Un JSON valido ma che non descrive una tabella (qui un numero) deve dare un
    # errore comprensibile, non un DataFrame senza senso più avanti nel flusso.
    with pytest.raises(ValueError, match="JSON"):
        read_any(_upload(b'42', "numero.json"))


def test_read_any_json_malformato_solleva():
    with pytest.raises(ValueError):  # json.JSONDecodeError è una sottoclasse
        read_any(_upload(b'{non-json}', "rotto.json"))
