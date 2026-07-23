"""
Controlli di trasparenza e sanità su una risposta. Funzioni pure: si verifica che
riconoscano le colonne usate e che gli avvisi scattino solo quando devono — un
sanity check che grida al lupo su risposte corrette è peggio che non averlo.
"""
import numpy as np
import pandas as pd

from nlda.checks import columns_referenced, sanity_warnings


# --- Colonne usate ---------------------------------------------------------------
def test_riconosce_le_colonne_citate_nel_codice():
    code = "result = df.groupby('Region')['Sales'].sum()"
    assert columns_referenced(code, ["Region", "Sales", "Quantity"]) == ["Region", "Sales"]


def test_ignora_le_stringhe_che_non_sono_colonne():
    code = "result = f'ciao {df[\"Sales\"].sum()}'"
    # 'ciao ...' non è una colonna: non deve comparire.
    assert columns_referenced(code, ["Sales"]) == ["Sales"]


def test_colonne_dedotte_da_una_lista_di_subscript():
    code = "result = df[['A', 'B']]"
    assert columns_referenced(code, ["A", "B", "C"]) == ["A", "B"]


def test_codice_non_parsabile_non_esplode():
    assert columns_referenced("df[[[", ["A"]) == []


def test_nessun_doppione_ordine_preservato():
    code = "df['A'] + df['B'] + df['A']"
    assert columns_referenced(code, ["A", "B"]) == ["A", "B"]


# --- Sanity check: scattano solo quando devono -----------------------------------
def test_tabella_vuota_avvisa():
    avvisi = sanity_warnings(pd.DataFrame({"a": []}))
    assert avvisi and "vuota" in avvisi[0]


def test_percentuale_fuori_intervallo_avvisa():
    df = pd.DataFrame({"cat": ["x"], "percentuale": [140.0]})
    avvisi = sanity_warnings(df)
    assert avvisi and "percentuale" in avvisi[0].lower()


def test_percentuale_valida_non_avvisa():
    df = pd.DataFrame({"cat": ["x", "y"], "percentuale": [60.0, 40.0]})
    assert sanity_warnings(df) == []


def test_variazione_oltre_cento_non_e_un_errore():
    # 'variazione_%' PUÒ superare il 100% (una misura raddoppia): non va segnalata,
    # a differenza di 'percentuale' che è una quota sul totale.
    df = pd.DataFrame({"periodo": ["Q1", "Q2"], "variazione_%": [np.nan, 150.0]})
    assert sanity_warnings(df) == []


def test_nan_e_infinito_avvisano():
    assert sanity_warnings(float("nan"))
    assert sanity_warnings(float("inf"))


def test_scalare_e_dataframe_normali_non_avvisano():
    assert sanity_warnings(840.0) == []
    assert sanity_warnings(pd.DataFrame({"a": [1, 2]})) == []
