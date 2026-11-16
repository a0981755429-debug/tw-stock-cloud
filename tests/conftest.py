import pandas as pd
import pytest

from stockapp import data


@pytest.fixture(autouse=True)
def _no_finmind_network(monkeypatch):
    """測試不連網：FinMind 一律回空表，需要資料的測試自己再 monkeypatch。"""
    monkeypatch.setattr(data, "finmind", lambda *a, **k: pd.DataFrame())


@pytest.fixture(autouse=True)
def _no_mops_network(monkeypatch):
    """綜合訊號會抓今日重大訊息，測試一律回空。"""
    from stockapp import analysts
    monkeypatch.setattr(analysts, "mops_today", lambda: {})
