"""Test del loader: rilevamento date, colonne-misura vs identificatori, lettura file, analyze."""
import io

import pandas as pd
import pytest

from nlda.config import Settings
from nlda.loader import (
    _maybe_parse_dates,
    analyze,
    dataset_signature,
    default_unit,
    measure_columns,
    monthly_trend,
    ordered_measures,
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


# --- Selettori del report: euristiche estratte da main.py ----------------------
def test_ordered_measures_mette_davanti_le_misure_principali():
    # Con molte colonne numeriche il default proposto all'utente deve essere
    # quello che si aspetta, non la prima in ordine alfabetico o di colonna.
    out = ordered_measures(["Quantity", "Discount", "Sales", "Profit"])
    assert out[:2] == ["Sales", "Profit"]
    assert set(out) == {"Quantity", "Discount", "Sales", "Profit"}


def test_ordered_measures_conserva_lordine_delle_altre():
    assert ordered_measures(["b", "a", "c"]) == ["b", "a", "c"]


def test_ordered_measures_su_lista_vuota():
    assert ordered_measures([]) == []


@pytest.mark.parametrize("misura", ["Sales", "profit", "Importo totale", "Costo unitario"])
def test_default_unit_riconosce_le_misure_economiche(misura):
    assert default_unit(misura) == "$"


@pytest.mark.parametrize("misura", ["Quantity", "Età", "punteggio", None, ""])
def test_default_unit_non_inventa_unita(misura):
    # Su misure non economiche nessuna unità è meglio di una sbagliata: comparirebbe
    # accanto a ogni numero nei KPI e nelle risposte dell'AI.
    assert default_unit(misura) == ""


# --- Firma del dataset ---------------------------------------------------------
def test_dataset_signature_stabile_sullo_stesso_contenuto(sales_df: pd.DataFrame):
    assert dataset_signature(sales_df, "x") == dataset_signature(sales_df.copy(), "x")


def test_dataset_signature_cambia_se_cambiano_i_valori(sales_df: pd.DataFrame):
    # È il caso che conta: stesso nome file, stesse colonne, dati diversi. Senza
    # l'hash del contenuto il report resterebbe quello del file precedente.
    modificato = sales_df.copy()
    modificato.loc[0, "Sales"] = 99999
    assert dataset_signature(sales_df, "x") != dataset_signature(modificato, "x")


def test_dataset_signature_cambia_con_la_sorgente(sales_df: pd.DataFrame):
    assert dataset_signature(sales_df, "a") != dataset_signature(sales_df, "b")


def test_dataset_signature_non_solleva_su_colonne_non_hashabili():
    # Celle con liste dentro: l'hash non è calcolabile, la firma deve degradare.
    df = pd.DataFrame({"a": [[1, 2], [3]]})
    firma = dataset_signature(df, "x")
    assert firma[0] == "x" and firma[-1] is None


# --- Limiti sul file caricato --------------------------------------------------
def _csv_finto(righe: int, colonne: int = 2):
    intestazione = ",".join(f"c{i}" for i in range(colonne))
    corpo = "\n".join(",".join(str(i) for _ in range(colonne)) for i in range(righe))
    buf = io.BytesIO(f"{intestazione}\n{corpo}\n".encode())
    buf.name = "grande.csv"  # type: ignore[attr-defined]
    return buf


def test_troppe_righe_vengono_rifiutate(monkeypatch):
    # Si RIFIUTA invece di campionare: un totale calcolato su un campione sarebbe
    # un numero sbagliato presentato come giusto, e l'app promette il contrario.
    monkeypatch.setattr("nlda.loader.settings", Settings(max_rows=10))
    with pytest.raises(ValueError, match="righe"):
        read_any(_csv_finto(righe=50))


def test_troppe_colonne_vengono_rifiutate(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_columns=3))
    with pytest.raises(ValueError, match="colonne"):
        read_any(_csv_finto(righe=5, colonne=10))


def test_il_messaggio_dice_cosa_fare(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_rows=10))
    with pytest.raises(ValueError) as errore:
        read_any(_csv_finto(righe=50))
    assert "MAX_ROWS" in str(errore.value)      # come alzare il limite
    assert "aggrega" in str(errore.value)       # e l'alternativa


def test_un_file_nei_limiti_passa(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_rows=100, max_columns=10))
    assert len(read_any(_csv_finto(righe=50))) == 50


# --- La firma è campionata: cosa distingue e cosa no --------------------------
def test_la_firma_distingue_un_cambio_in_testa_e_in_coda():
    a = pd.DataFrame({"x": range(1000), "y": ["v"] * 1000})
    testa = a.copy()
    testa.loc[0, "x"] = 99999
    coda = a.copy()
    coda.loc[999, "x"] = 99999
    assert dataset_signature(a, "f") != dataset_signature(testa, "f")
    assert dataset_signature(a, "f") != dataset_signature(coda, "f")


def test_la_firma_distingue_un_cambio_di_TIPO():
    # I dtype entrano nella firma proprio per stringere il campionamento: una
    # colonna che cambia tipo è un dataset diverso anche a valori identici.
    a = pd.DataFrame({"x": range(1000)})
    b = a.copy()
    b["x"] = b["x"].astype(float)
    assert dataset_signature(a, "f") != dataset_signature(b, "f")


def test_limite_noto_e_dichiarato_del_campionamento():
    """
    Il centro del dataset NON entra nella firma: è il prezzo dichiarato per
    renderla O(1) su un percorso che gira a ogni interazione.

    Il test esiste per fissare il compromesso, non per approvarlo: se un domani
    si volesse una firma esatta, questo test diventa rosso e obbliga a decidere
    consapevolmente invece di scoprirlo per caso.
    """
    a = pd.DataFrame({"x": range(5000)})
    centro = a.copy()
    centro.loc[2500, "x"] = 99999
    assert dataset_signature(a, "f") == dataset_signature(centro, "f")


def test_la_firma_e_indipendente_dalla_dimensione(sales_df: pd.DataFrame):
    # Non una misura di tempo (fragile in CI) ma della proprietà che la causa:
    # si hashano al più 400 righe, quante che ne abbia il dataset.
    import nlda.loader as mod
    grande = pd.concat([sales_df] * 2000, ignore_index=True)
    chiamate = []
    originale = pd.util.hash_pandas_object
    mod.pd.util.hash_pandas_object = lambda obj, **kw: chiamate.append(len(obj)) or originale(obj, **kw)
    try:
        dataset_signature(grande, "f")
    finally:
        mod.pd.util.hash_pandas_object = originale
    assert chiamate and chiamate[0] <= 2 * mod._RIGHE_CAMPIONE, chiamate
