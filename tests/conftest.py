"""Fixture condivise dai test."""
import pandas as pd
import pytest


@pytest.fixture
def sales_df() -> pd.DataFrame:
    """Un piccolo dataset 'vendite' con categoria, misura e data (come lo usa l'app)."""
    return pd.DataFrame({
        "Region": ["North", "South", "North", "West", "South", "West"],
        "Sales": [100, 200, 150, 80, 220, 90],
        "Profit": [10, 40, 20, 5, 50, 8],
        "Order Date": pd.to_datetime(
            ["2023-01-05", "2023-01-20", "2023-02-11",
             "2023-02-15", "2023-03-02", "2023-03-19"]),
    })
