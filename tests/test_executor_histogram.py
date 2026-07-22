"""
Test dell'istogramma adattivo (nlda.charts.histogram).

Garantisce che la scelta della scala si adatti a QUALSIASI dataset caricato:
scala logaritmica solo su dati positivi e fortemente asimmetrici, altrimenti
lineare — senza mai rompersi su zeri, negativi o colonne non numeriche.
"""
import numpy as np
import pandas as pd
import pytest

from nlda.charts import histogram


def _x_title(fig) -> str:
    return fig.layout.xaxis.title.text or ""


def test_dati_lognormali_positivi_usano_scala_log():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"importo": rng.lognormal(mean=4, sigma=1.5, size=5000)})
    fig = histogram(df, "importo")
    assert "logaritmica" in _x_title(fig)


def test_distribuzione_regolare_resta_lineare():
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"eta": rng.normal(45, 12, 5000).clip(18, 90)})
    fig = histogram(df, "eta")
    assert "logaritmica" not in _x_title(fig)


def test_valori_negativi_non_usano_log():
    # Con zeri/negativi log10 non è definito: deve restare lineare senza errori.
    rng = np.random.default_rng(2)
    vals = np.concatenate([rng.exponential(200, 5000), [-50.0, -300.0, 0.0]])
    df = pd.DataFrame({"profit": vals})
    fig = histogram(df, "profit")
    assert "logaritmica" not in _x_title(fig)


def test_lineare_asimmetrico_annota_gli_outlier():
    rng = np.random.default_rng(3)
    # Positivi ma con zeri (min == 0) → niente log; coda lunga → nota sugli outlier.
    vals = np.concatenate([rng.exponential(100, 5000), [0.0], [5e4, 6e4, 7e4]])
    df = pd.DataFrame({"x": vals})
    fig = histogram(df, "x")
    assert "logaritmica" not in _x_title(fig)
    assert any("percentile" in (a.text or "") for a in fig.layout.annotations)


def test_colonna_senza_valori_numerici_solleva():
    df = pd.DataFrame({"testo": ["a", "b", "c"]})
    with pytest.raises(ValueError):
        histogram(df, "testo")
