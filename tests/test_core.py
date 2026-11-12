import json

import numpy as np
import pandas as pd
import pytest

from stockapp import alerts, config, daily, data, intraday
from stockapp.indicators import kd, rsi, sma
from stockapp.signals import analyze


def make_hist(closes):
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": 1000.0}, index=idx)


def test_indicators_basic():
    s = pd.Series(range(1, 31), dtype=float)
    assert sma(s, 5).iloc[-1] == 28
    assert rsi(s).iloc[-1] == 100            # 只漲不跌
    k = kd(s * 1.01, s * 0.99, s)
    assert k["k"].iloc[-1] > 80


def test_uptrend_is_bullish_and_downtrend_bearish():
    wiggle = np.tile([0, 2, -1, 1, -2, 0], 20)  # 有漲有跌，避免 RSI 卡在 0 / 100
    up = analyze(make_hist(np.linspace(100, 140, 120) + wiggle))
    down = analyze(make_hist(np.linspace(140, 100, 120) + wiggle))
    assert up.label == "偏多" and up.score > 0
    assert down.label == "偏空" and down.score < 0
    assert any("多頭排列" in r for r in up.reasons)


def test_short_history():
    assert analyze(make_hist([100] * 10)).label == "資料不足"


def test_golden_cross_event():
    closes = list(np.linspace(120, 100, 80)) + [100, 101, 103, 106, 110, 115]
    found = False
    for n in range(81, len(closes) + 1):
        if any("黃金交叉" in e for e in analyze(make_hist(closes[:n])).events):
            found = True
    assert found


def test_parse_mis_fallbacks():
    q = data.parse_mis({"c": "2330", "n": "台積電", "z": "1050.0000", "y": "1000.0000", "d": "20261112", "t": "10:00:00"})
    assert q.price == 1050 and q.is_live and round(q.change_pct, 2) == 5.0
    q = data.parse_mis({"c": "2330", "z": "-", "a": "1051.0000_1052.0000_", "b": "1049.0000_1048.0000_", "y": "1000"})
    assert q.price == 1050 and q.is_live
    q = data.parse_mis({"c": "2330", "z": "-", "a": "-", "b": "-", "y": "1000"})
    assert q.price == 1000 and not q.is_live


def _q(price, prev=1000.0, live=True):
    return data.Quote("2330", "台積電", price, prev, None, None, None, None, "10:00:00", "20261112", live)


def test_alerts_once_per_day():
    stocks = [config.Stock("2330", below=950, above=1100, change_pct=5)]
    state = {}
    msgs = alerts.check(stocks, {"2330": _q(940)}, {"2330": "台積電"}, state, "20261112")
    assert len(msgs) == 2  # 跌破 + 跌幅 6%
    assert alerts.check(stocks, {"2330": _q(930)}, {}, state, "20261112") == []
    # 隔天重新計算
    assert len(alerts.check(stocks, {"2330": _q(930)}, {}, state, "20261113")) == 2
    assert list(state) == ["20261113"]


def test_alerts_ignore_stale_quote():
    stocks = [config.Stock("2330", below=950)]
    assert alerts.check(stocks, {"2330": _q(900, live=False)}, {}, {}, "20261112") == []


def test_load_watchlist(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text('stocks:\n  - id: "2330"\n    alert:\n      below: 900\n  - "0050"\n  - id: 2330\n', encoding="utf-8")
    s = config.load(p)
    assert [x.id for x in s.stocks] == ["2330", "0050"]
    assert s.stocks[0].below == 900 and s.stocks[1].below is None


def test_repo_watchlist_is_valid():
    s = config.load()
    assert s.stocks and all(x.id for x in s.stocks)


def test_daily_build(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "price_history", lambda sid, m: make_hist(np.linspace(100, 150, 130)))
    monkeypatch.setattr(data, "fundamentals", lambda sid: {"eps": [], "revenue": []})
    monkeypatch.setattr(data, "stock_info", lambda: {"2330": {"name": "台積電", "market": "twse", "industry": "半導體業"}})
    monkeypatch.setattr(config, "DOCS", tmp_path)
    monkeypatch.setattr(config, "load", lambda: config.Settings([config.Stock("2330")]))
    sent = []
    monkeypatch.setattr(daily.notify, "telegram", lambda t: sent.append(t) or True)
    assert daily.main([]) == 0
    out = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))
    st = out["stocks"][0]
    assert st["name"] == "台積電" and st["close"] == 150 and len(st["chart"]) == 120
    assert sent and "台積電" in sent[0]


def test_intraday_flow(monkeypatch, tmp_path):
    (tmp_path / "data.json").write_text(json.dumps({"stocks": [{"id": "2330", "name": "台積電", "market": "上市"}]}), encoding="utf-8")
    monkeypatch.setattr(config, "DOCS", tmp_path)
    monkeypatch.setattr(config, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(config, "load", lambda: config.Settings([config.Stock("2330", below=950)]))
    monkeypatch.setattr(config, "load_state", lambda: {})
    saved = {}
    monkeypatch.setattr(config, "save_state", lambda s: saved.update(s))
    monkeypatch.setattr(data, "live_quotes", lambda pairs: {"2330": _q(940)})
    sent = []
    monkeypatch.setattr(intraday.notify, "telegram", lambda t: sent.append(t) or True)
    assert intraday.main(["--force"]) == 0
    assert sent and "跌到 940" in sent[0]
    assert any("2330:below" in v for v in saved.values())
    q = json.loads((tmp_path / "quotes.json").read_text(encoding="utf-8"))
    assert q["quotes"]["2330"]["price"] == 940
