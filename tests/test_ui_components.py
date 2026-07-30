"""
Test dei componenti di presentazione.

`build_kpis` (ora in `nlda.kpis`) è puro per costruzione: è la
parte della UI che si può testare davvero, ed è anche quella che l'utente colpisce per
prima: viene chiamata prima di qualsiasi altro rendering. Qui la copriamo soprattutto
sui dataset DEGENERI (vuoti, colonne tutte-NaN), perché è lì che una card "Top ..."
può non avere alcuna riga da mostrare.

Le funzioni di rendering vero (`render_value`, `render_result`, `render_linked_charts`)
si testano sostituendo l'intero modulo `st` con un doppio: non si avvia l'app (troppo
lento e con effetti collaterali), si verifica CHE COSA viene chiesto a Streamlit —
quale widget, con quali dati e con quale chiave. Le chiavi contano: due widget con la
stessa chiave fanno esplodere Streamlit a runtime.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

import nlda.ui_components as ui
from nlda.kpis import build_kpis
from nlda.loader import Grouped
from nlda.results import ExecutionFailure, ExecutionSuccess
from nlda.ui_components import (
    render_linked_charts,
    render_result,
    render_value,
)


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


# --- Rendering: cosa viene chiesto a Streamlit ---------------------------------
@pytest.fixture
def st_finto(monkeypatch) -> MagicMock:
    """
    Sostituisce il modulo `st` dentro ui_components con un doppio che registra le
    chiamate. Serve a testare il rendering senza avviare l'app: `AppTest` farebbe
    partire Streamlit per davvero (decine di secondi) per verificare le stesse cose.
    """
    fake = MagicMock()
    # `st.columns(n)` deve restituire n context manager distinti, come l'originale.
    fake.columns.side_effect = lambda n: [MagicMock() for _ in range(n)]
    # Nessuna barra selezionata, se un test non dice diversamente.
    fake.plotly_chart.return_value = SimpleNamespace(selection=SimpleNamespace(points=[]))
    monkeypatch.setattr(ui, "st", fake)
    return fake


def test_render_value_dataframe_va_in_tabella(st_finto: MagicMock):
    render_value(pd.DataFrame({"Region": ["North"], "Sales": [100.0]}))
    st_finto.dataframe.assert_called_once()
    assert st_finto.dataframe.call_args.kwargs["key"] == "r_df"
    st_finto.metric.assert_not_called()


def test_render_value_series_va_in_tabella_con_chiave_propria(st_finto: MagicMock):
    # Chiave diversa da quella del DataFrame: nello stesso turno possono comparire
    # entrambi, e chiavi duplicate sono un errore di Streamlit.
    render_value(pd.Series([1, 2, 3], name="Sales"))
    assert st_finto.dataframe.call_args.kwargs["key"] == "r_ser"


def test_render_value_scalare_diventa_una_metrica(st_finto: MagicMock):
    render_value(1234.56)
    st_finto.metric.assert_called_once()
    # Il numero è formattato all'italiana (punto per le migliaia), non lasciato grezzo.
    assert st_finto.metric.call_args.args[1] == "1.235"


def test_render_value_stringa_va_in_markdown(st_finto: MagicMock):
    render_value("Il totale è 100")
    assert "Il totale è 100" in st_finto.markdown.call_args.args[0]


def test_render_value_none_non_mostra_nulla(st_finto: MagicMock):
    # Un codice che produce solo una figura ha value=None: non deve comparire
    # una tabella vuota sotto il grafico.
    render_value(None)
    st_finto.dataframe.assert_not_called()
    st_finto.metric.assert_not_called()
    st_finto.markdown.assert_not_called()


def test_render_value_ignora_il_messaggio_segnaposto(st_finto: MagicMock):
    # "Codice eseguito correttamente." è il segnaposto dell'executor quando non
    # c'è un risultato: mostrarlo come "Risultato: ..." confonderebbe l'utente.
    render_value("Codice eseguito correttamente.")
    st_finto.markdown.assert_not_called()


def test_render_result_mostra_risposta_grafico_dati_e_codice(st_finto: MagicMock):
    import plotly.express as px

    fig = px.bar(pd.DataFrame({"x": ["a"], "y": [1]}), x="x", y="y")
    risultato = ExecutionSuccess(fig=fig, value=pd.DataFrame({"Sales": [1.0]}), summary="ok")
    render_result("df['Sales'].sum()", risultato, explanation="Le vendite salgono.")

    assert st_finto.plotly_chart.call_args.kwargs["key"] == "r_fig"
    st_finto.dataframe.assert_called_once()
    st_finto.code.assert_called_once_with("df['Sales'].sum()", language="python")
    st_finto.error.assert_not_called()


def test_render_result_senza_figura_non_disegna_grafici(st_finto: MagicMock):
    render_result("df['Sales'].sum()", ExecutionSuccess(fig=None, value=42, summary="ok"))
    st_finto.plotly_chart.assert_not_called()
    st_finto.metric.assert_called_once()


def test_render_result_fallimento_mostra_solo_il_messaggio(st_finto: MagicMock):
    # `kind` è la causa tecnica su cui decide il codice (retry, log): all'utente si
    # mostra solo il messaggio. Vederlo trapelare nella UI sarebbe una regressione.
    fallimento = ExecutionFailure(kind="security", message="Operazione non consentita.",
                                  code="import os")
    render_result("import os", fallimento)

    st_finto.error.assert_called_once_with("Operazione non consentita.")
    st_finto.plotly_chart.assert_not_called()
    st_finto.dataframe.assert_not_called()
    for chiamata in st_finto.mock_calls:
        assert "security" not in str(chiamata)
    # Il codice rifiutato resta comunque visibile: è ciò che l'utente deve capire.
    st_finto.code.assert_called_once_with("import os", language="python")


def test_render_result_avvisa_su_colonna_inventata_nella_domanda(st_finto: MagicMock):
    # La domanda nomina 'Fatturato' (inesistente) e il codice usa Sales: va mostrato
    # un avviso in chiaro, con la colonna su cui la risposta si basa davvero.
    risultato = ExecutionSuccess(fig=None, value=2261537.0, summary="ok")
    render_result("df['Sales'].sum()", risultato, columns=["Sales", "Region"],
                  question="somma della colonna Fatturato")
    avvisi = [str(c) for c in st_finto.warning.call_args_list]
    assert any("Fatturato" in a and "Sales" in a for a in avvisi)


def test_render_result_niente_avviso_se_la_colonna_e_reale(st_finto: MagicMock):
    render_result("df['Sales'].sum()", ExecutionSuccess(fig=None, value=1.0, summary="ok"),
                  columns=["Sales", "Region"], question="somma della colonna Sales")
    st_finto.warning.assert_not_called()


def _insights(df: pd.DataFrame) -> dict:
    """Insight nella forma prodotta da `analyze` per classifica e andamento."""
    top = (df.groupby("Region", as_index=False)["Sales"].sum()
             .sort_values("Sales", ascending=False))
    per = ui.monthly_trend(df, "Order Date", "Sales")
    return {
        "top": Grouped("Region", "Sales", top),
        "trend": Grouped("Order Date", "Sales", per),
        "measure": "Sales",
    }


def _figure(df: pd.DataFrame, insights: dict):
    from nlda.charts import to_chart

    return (to_chart(insights["top"].data, kind="bar"),
            to_chart(insights["trend"].data, kind="line"))


def test_render_linked_charts_senza_figure_non_fa_nulla(st_finto: MagicMock):
    render_linked_charts(pd.DataFrame(), {}, None, None)
    st_finto.columns.assert_not_called()
    st_finto.plotly_chart.assert_not_called()


def test_render_linked_charts_affianca_i_due_grafici(sales_df: pd.DataFrame, st_finto: MagicMock):
    ins = _insights(sales_df)
    top_fig, trend_fig = _figure(sales_df, ins)
    render_linked_charts(sales_df, ins, top_fig, trend_fig)

    st_finto.columns.assert_called_once_with(2)
    chiavi = [c.kwargs["key"] for c in st_finto.plotly_chart.call_args_list]
    assert chiavi == ["report_top", "report_trend"]
    st_finto.caption.assert_called_once()  # istruzioni sul click solo quando serve


def test_render_linked_charts_una_sola_figura_usa_una_colonna(sales_df: pd.DataFrame,
                                                              st_finto: MagicMock):
    ins = _insights(sales_df)
    top_fig, _ = _figure(sales_df, ins)
    render_linked_charts(sales_df, ins, top_fig, None)

    st_finto.columns.assert_called_once_with(1)
    st_finto.caption.assert_not_called()  # senza andamento non c'è nulla da filtrare
    assert st_finto.plotly_chart.call_args.kwargs["key"] == "report_top"


def test_click_sulla_barra_filtra_landamento(sales_df: pd.DataFrame, st_finto: MagicMock):
    # Il collegamento tra i due grafici è la sola interazione del report: se si
    # rompe, cliccare una barra non cambia più nulla e nessun errore lo segnala.
    st_finto.plotly_chart.return_value = SimpleNamespace(
        selection=SimpleNamespace(points=[{"x": "South"}])
    )
    ins = _insights(sales_df)
    top_fig, trend_fig = _figure(sales_df, ins)
    render_linked_charts(sales_df, ins, top_fig, trend_fig)

    chiavi = [c.kwargs["key"] for c in st_finto.plotly_chart.call_args_list]
    assert chiavi == ["report_top", "report_trend_filtered"]
    titoli = " ".join(str(c.args[0]) for c in st_finto.markdown.call_args_list)
    assert "South" in titoli


def test_selezione_di_una_categoria_inesistente_ripiega_sul_grafico_intero(
        sales_df: pd.DataFrame, st_finto: MagicMock):
    # Le etichette lunghe vengono troncate con "…" nel grafico: il match esatto
    # fallisce e si ripiega sul prefisso. Con una categoria del tutto assente non
    # deve restare né un errore né una serie vuota.
    st_finto.plotly_chart.return_value = SimpleNamespace(
        selection=SimpleNamespace(points=[{"x": "Regione Inesistente"}])
    )
    ins = _insights(sales_df)
    top_fig, trend_fig = _figure(sales_df, ins)
    render_linked_charts(sales_df, ins, top_fig, trend_fig)

    chiavi = [c.kwargs["key"] for c in st_finto.plotly_chart.call_args_list]
    assert chiavi[-1] in ("report_trend", "report_trend_filtered")


# --- #34: la barra cliccata si identifica dal valore ESATTO in customdata --------
def test_clicked_category_preferisce_customdata():
    # Etichetta troncata sull'asse, valore intero in customdata: si usa quello.
    punto = {"y": "Categoria molto lung…", "x": 42,
             "customdata": ["Categoria molto lunga e per intero"]}
    assert ui._clicked_category(punto) == "Categoria molto lunga e per intero"


def test_clicked_category_customdata_scalare():
    assert ui._clicked_category({"customdata": "Esatto", "x": 1}) == "Esatto"


def test_clicked_category_fallback_su_x_stringa():
    # Barre verticali con etichette corte: nessun customdata, la categoria è su x.
    assert ui._clicked_category({"x": "Nord", "y": 100}) == "Nord"


def test_clicked_category_fallback_su_y_se_x_non_e_stringa():
    # Barre orizzontali: x è la misura numerica, la categoria è su y.
    assert ui._clicked_category({"x": 100, "y": "Sud"}) == "Sud"


# --- Dimensione del valore nelle card KPI --------------------------------------
# `readout` non decide il font in pixel: passa al CSS un coefficiente `--vfs` che il
# browser moltiplica per la larghezza della card. Il calcolo sta in Python perché è
# l'unico punto che conosce la LUNGHEZZA del valore, e senza quello i numeri lunghi
# uscivano dal riquadro (misurato: con 4 card in fila a ~1580px di finestra il valore
# aveva 2px di margine — bastava una cifra in più).
def _vfs(value: str) -> float:
    """Estrae il coefficiente --vfs dall'HTML generato da `readout`."""
    col = MagicMock()
    ui.readout(col, "Etichetta", value)
    html_reso = col.markdown.call_args.args[0]
    return float(html_reso.split("--vfs:")[1].split("'")[0])


def test_valore_lungo_riceve_un_coefficiente_piu_piccolo():
    assert _vfs("1.284.377.905,40 €") < _vfs("9.412")


def test_coefficiente_proporzionale_alla_lunghezza():
    # 153 / 17 caratteri, arrotondato a un decimale.
    assert _vfs("19.981.676.855 $") == pytest.approx(153 / 16, abs=0.05)


def test_valore_vuoto_non_divide_per_zero():
    # Una card può nascere senza valore (dataset degenere): non deve esplodere.
    assert _vfs("") == pytest.approx(153, abs=0.05)
