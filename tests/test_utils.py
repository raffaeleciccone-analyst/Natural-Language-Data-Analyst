"""Test delle utility condivise (formattazione numerica IT, pulizia codice, tipi colonna)."""
import pandas as pd

from core.utils import clean_code, column_kind, fmt_num


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
