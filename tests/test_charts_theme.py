"""
Coerenza cromatica del tema dei grafici.

Regressione mirata: i grafici che l'agente genera con `px.bar` diretto nascevano
del blu di default di Plotly, mentre quelli del report (via `to_chart`) erano teal.
`apply_theme` deve uniformarli riassegnando il colore traccia per traccia, perché
`layout.colorway` da solo non tocca le tracce già create da Plotly Express.
"""
import plotly.express as px

from nlda.charts import _BAR, apply_theme

_TEAL = "#0d8a7d"


def test_barre_generate_alla_px_diventano_teal():
    # Come il codice dell'agente: px.bar senza color_discrete_sequence → blu default.
    fig = px.bar(x=["A", "B", "C"], y=[3, 1, 2])
    apply_theme(fig)
    assert fig.data[0].marker.color == _TEAL == _BAR


def test_linea_generata_alla_px_diventa_teal():
    fig = px.line(x=[1, 2, 3], y=[4, 5, 6], markers=True)
    apply_theme(fig)
    assert fig.data[0].line.color == _TEAL


def test_serie_multiple_prendono_colori_in_sequenza():
    # Un grafico multi-serie (px assegna un colore per categoria) deve seguire la
    # NOSTRA colorway, non quella di default: prima traccia teal, poi le successive.
    fig = px.bar(x=["A", "B"], y=[1, 2], color=["x", "y"])
    apply_theme(fig)
    colori = [tr.marker.color for tr in fig.data]
    assert colori[0] == _TEAL
    assert colori[1] != _TEAL           # la seconda serie è un colore distinto
    assert len(set(colori)) == len(colori)


def test_heatmap_non_viene_ricolorata():
    # Le scale continue (correlazioni) usano una colorscale, non un marker a tinta
    # unita: apply_theme non deve toccarle.
    fig = px.imshow([[1.0, 0.2], [0.2, 1.0]], zmin=-1, zmax=1)
    apply_theme(fig)
    # La traccia heatmap non ha un marker.color a tinta unita da sovrascrivere:
    # basta che apply_theme non sollevi e lasci il tipo intatto.
    assert fig.data[0].type in ("heatmap", "image")
