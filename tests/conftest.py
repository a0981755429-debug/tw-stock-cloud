import pandas as pd
import pytest

from stockapp import data


@pytest.fixture(autouse=True)
def _no_finmind_network(monkeypatch):
    """測試不連網：FinMind 一律回空表，需要資料的測試自己再 monkeypatch。"""
    monkeypatch.setattr(data, "finmind", lambda *a, **k: pd.DataFrame())
