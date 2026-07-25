"""
Controlli di trasparenza e sanità su una risposta. Funzioni pure: si verifica che
riconoscano le colonne usate e che gli avvisi scattino solo quando devono — un
sanity check che grida al lupo su risposte corrette è peggio che non averlo.
"""
import numpy as np
import pandas as pd

from nlda.checks import (
    claimed_missing_columns,
    columns_referenced,
    sanity_warnings,
    unknown_columns_referenced,
)


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


# --- Colonne inventate: lette da df ma inesistenti -------------------------------
def test_colonna_inventata_rilevata():
    assert unknown_columns_referenced("df['Fatturato'].mean()", ["Sales", "Region"]) == \
        ["Fatturato"]


def test_colonna_reale_non_e_segnalata():
    assert unknown_columns_referenced("df['Sales'].mean()", ["Sales", "Region"]) == []


def test_creare_una_colonna_non_e_inventarla():
    # df['nuova'] = ... CREA una colonna (contesto Store): non è un accesso a una
    # colonna fantasma e non va segnalato.
    code = "df['margine'] = df['Profit'] / df['Sales']"
    assert unknown_columns_referenced(code, ["Profit", "Sales"]) == []


def test_frame_derivato_non_e_controllato():
    # 'percentuale' è una colonna nuova di un frame DERIVATO (detail), legittima:
    # si guardano solo i subscript sul nome `df`.
    code = ("detail = df.groupby('Region', as_index=False)['Sales'].sum()\n"
            "detail['percentuale'] = detail['Sales'] / detail['Sales'].sum()\n"
            "result = detail")
    assert unknown_columns_referenced(code, ["Region", "Sales"]) == []


def test_maschera_booleana_non_da_falsi_positivi():
    # df[df['Sales'] > 100]: la chiave è una Series booleana, non una stringa.
    assert unknown_columns_referenced("df[df['Sales'] > 100]", ["Sales"]) == []


def test_lista_di_subscript_con_una_inventata():
    assert unknown_columns_referenced("df[['Region', 'Ignota']]", ["Region", "Sales"]) == \
        ["Ignota"]


def test_chiave_variabile_ignorata():
    # df[col] con col variabile: non è una stringa letterale, non si può giudicare.
    assert unknown_columns_referenced("col = 'x'\nresult = df[col]", ["Sales"]) == []


def test_codice_non_parsabile_non_esplode_ignote():
    assert unknown_columns_referenced("df[[[", ["A"]) == []


# --- Colonne NOMINATE dalla domanda ma inesistenti -------------------------------
_COLS = ["Region", "Sales", "Order Date", "Order ID", "Sub-Category"]


def test_domanda_nomina_una_colonna_inventata():
    assert claimed_missing_columns("Qual è la somma della colonna Fatturato?", _COLS) == \
        ["Fatturato"]


def test_domanda_con_df_subscript_inventato():
    assert claimed_missing_columns("dammi df['Zorglub'] per favore", _COLS) == ["Zorglub"]


def test_colonna_reale_nominata_non_avvisa():
    assert claimed_missing_columns("media della colonna Sales", _COLS) == []


def test_colonna_composta_reale_non_e_spezzata():
    # 'Order Date' è una colonna reale: il pattern la cattura intera, non 'Order'.
    assert claimed_missing_columns("mostrami la colonna Order Date", _COLS) == []


def test_parola_funzione_dopo_colonna_non_e_falso_positivo():
    # "la colonna con più vendite": 'con' è minuscola, non un nome di colonna.
    assert claimed_missing_columns("Qual è la colonna con più vendite?", _COLS) == []


def test_nessuna_menzione_esplicita_non_avvisa():
    # Nessun 'colonna/campo/df[...]': non si inventano avvisi su valori o concetti.
    assert claimed_missing_columns("Mostrami le vendite per regione", _COLS) == []


def test_piu_colonne_inventate_dedup_ordine():
    q = "somma della colonna Fatturato e della colonna Fatturato, più df['Ghost']"
    assert claimed_missing_columns(q, _COLS) == ["Fatturato", "Ghost"]


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
