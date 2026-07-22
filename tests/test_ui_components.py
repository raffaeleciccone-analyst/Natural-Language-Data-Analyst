"""
Test dei componenti di presentazione che NON toccano Streamlit.

`build_kpis` è puro per costruzione (ritorna tuple, nessun effetto Streamlit): è la
parte della UI che si può testare davvero, ed è anche quella che l'utente colpisce per
prima: viene chiamata prima di qualsiasi altro rendering. Qui la copriamo soprattutto
sui dataset DEGENERI (vuoti, colonne tutte-NaN), perché è lì che una card "Top ..."
può non avere alcuna riga da mostrare.
"""
import pandas as pd
import pytest

from nlda.ui_components import build_kpis


@pytest.fixture
def empty_df() -> pd.DataFrame:
    """Un CSV con le sole intestazioni: caso reale di upload da parte dell'utente."""
    return pd.DataFrame({
        "Region": pd.Series(dtype="object"),
        "Sales": pd.Series(dtype="float64"),
    })


# --- Caso nominale -------------------------------------------------------------
def test_kpi_con_misura_e_categoria(sales_df: pd.DataFrame):
    kpis = build_kpis(sales_df, "Sales", "Region", "")
    assert len(kpis) == 4
    etichette = [k[0] for k in kpis]
    assert etichette == ["Totale Sales", "Media Sales", "Massimo Sales", "Top Region"]
    # South = 200 + 220 = 420, il totale più alto
    assert kpis[3][1] == "South"


def test_unita_di_misura_accostata_ai_valori(sales_df: pd.DataFrame):
    kpis = build_kpis(sales_df, "Sales", "Region", "€")
    assert kpis[0][1].endswith(" €")


def test_kpi_senza_misura_usa_i_conteggi(sales_df: pd.DataFrame):
    kpis = build_kpis(sales_df, None, "Region", "")
    assert [k[0] for k in kpis] == ["Record", "Region distinte", "Top Region"]


def test_kpi_senza_misura_ne_categoria(sales_df: pd.DataFrame):
    kpis = build_kpis(sales_df, None, None, "")
    assert [k[0] for k in kpis] == ["Record", "Colonne"]


# --- Dataset degeneri: non devono MAI sollevare --------------------------------
def test_dataset_vuoto_non_solleva(empty_df: pd.DataFrame):
    # Regressione: `leader.index[0]` su una classifica vuota sollevava IndexError e
    # l'app mostrava la schermata d'errore di Streamlit al primo rendering.
    kpis = build_kpis(empty_df, "Sales", "Region", "")
    assert len(kpis) == 4
    assert kpis[3][0] == "Record"  # ripiego: niente leader calcolabile


def test_dataset_vuoto_senza_misura_non_solleva(empty_df: pd.DataFrame):
    kpis = build_kpis(empty_df, None, "Region", "")
    assert len(kpis) == 2  # Record + distinte, senza la card 'Top'
    assert all(not k[0].startswith("Top") for k in kpis)


def test_categoria_tutta_mancante_non_solleva():
    # Righe presenti ma categoria tutta NaN: il groupby scarta i NaN e la classifica
    # esce vuota pur non essendo vuoto il DataFrame.
    df = pd.DataFrame({"Region": [None, None, None], "Sales": [1.0, 2.0, 3.0]})
    kpis = build_kpis(df, "Sales", "Region", "")
    assert len(kpis) == 4
    assert kpis[3][0] == "Record"


def test_ogni_kpi_ha_la_forma_attesa(empty_df: pd.DataFrame):
    # main.py spacchetta 5 valori per card e chiama st.columns(len(kpis)):
    # la forma e la non-vuotezza sono un contratto, non un dettaglio.
    for kpis in (build_kpis(empty_df, "Sales", "Region", ""),
                 build_kpis(empty_df, None, None, "")):
        assert kpis, "build_kpis non deve mai restituire una lista vuota"
        for label, value, sub, tick, small in kpis:
            assert isinstance(label, str) and label
            assert isinstance(value, str)
            assert isinstance(sub, str)
            assert tick.startswith("#")
            assert isinstance(small, bool)
