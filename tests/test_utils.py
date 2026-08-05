"""Test delle utility condivise (formattazione numerica IT, pulizia codice, tipi colonna)."""
import pandas as pd

from nlda.utils import clean_code, column_kind, fmt_num, with_unit


def test_clean_code_rimuove_fence_markdown():
    assert clean_code("```python\ndf.sum()\n```") == "df.sum()"
    assert clean_code("```\nx = 1\n```") == "x = 1"
    assert clean_code("df.sum()") == "df.sum()"


def test_fmt_num_migliaia_italiane():
    assert fmt_num(2261537) == "2.261.537"
    assert fmt_num(1000) == "1.000"


def test_fmt_num_decimali():
    assert fmt_num(230.77) == "230,77"


def test_fmt_num_nan_e_non_numerico():
    assert fmt_num(float("nan")) == "—"
    assert fmt_num("ciao") == "ciao"


def test_column_kind():
    assert column_kind(pd.Series([1, 2, 3])) == "numerica"
    assert column_kind(pd.Series(["a", "b"])) == "testo"
    assert column_kind(pd.Series([True, False])) == "booleana"
    assert column_kind(pd.to_datetime(pd.Series(["2023-01-01", "2023-02-01"]))) == "data"


def test_with_unit_antepone_lunita():
    out = with_unit("Totale: 1000", "€")
    assert out.startswith("L'unità di misura dei valori è '€'.")
    assert "Totale: 1000" in out


def test_with_unit_senza_unita_lascia_il_testo_intatto():
    # Senza unità NON si deve aggiungere una frase: il modello la userebbe come
    # informazione e inventerebbe un'unità che i dati non hanno.
    assert with_unit("Totale: 1000", "") == "Totale: 1000"


def test_fmt_num_non_esplode_sull_infinito():
    """
    Regressione da una sessione di test sulla demo (5 agosto 2026): un CSV con
    `inf` fra i valori faceva rispondere **500** all'INTERO report. La causa era
    qui — `int(x)` su un infinito solleva `OverflowError` — e da qui si
    propagava a ogni numero mostrato dall'app.
    """
    assert fmt_num(float("inf")) == "∞"
    assert fmt_num(float("-inf")) == "−∞"


def test_fmt_num_distingue_infinito_da_mancante():
    """Un valore infinito NON è un valore mancante: confonderli nasconderebbe un
    dato anomalo dietro il trattino dei buchi."""
    assert fmt_num(float("nan")) == "—"
    assert fmt_num(float("inf")) != fmt_num(float("nan"))


def test_i_numeri_fuori_scala_non_diventano_righe_di_trecento_cifre():
    """
    `1e308` si stampava con 309 cifre: nella struttura delle colonne sfondava la
    tabella, in una card KPI il riquadro. Oltre 2^53 quelle cifre non sono
    nemmeno un dato — sono la codifica binaria che affiora.
    """
    assert fmt_num(1e308) == "1,00e+308"
    assert len(fmt_num(1e308)) < 15


def test_un_numero_piccolissimo_non_si_stampa_come_zero():
    """Il difetto opposto: `1e-10` diventava "0,00", cioè un numero diverso."""
    assert fmt_num(1e-10) == "1,00e-10"
    assert fmt_num(0.004) == "0,0040"   # piccolo ma leggibile: bastano più decimali


def test_i_numeri_normali_non_sono_cambiati():
    """Il rischio di una regola nuova è che tocchi il caso di tutti i giorni."""
    assert fmt_num(2261537) == "2.261.537"
    assert fmt_num(230.77) == "230,77"
    assert fmt_num(0) == "0"
