import json

import numpy as np
import pandas as pd
import pytest

from stockapp import alerts, config, daily, data, holdings, intraday
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
    monkeypatch.setattr(data, "live_quotes", lambda pairs: {})
    monkeypatch.setattr(holdings, "load", lambda: [])
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
    monkeypatch.setattr(holdings, "load", lambda: [holdings.Holding("2330", 1000, 1_000_000, 945, None)])
    sent = []
    monkeypatch.setattr(intraday.notify, "telegram", lambda t: sent.append(t) or True)
    assert intraday.main(["--force"]) == 0
    assert sent and "跌到 940" in sent[0] and "停損提醒" in sent[0]
    assert any("2330:below" in v for v in saved.values())
    q = json.loads((tmp_path / "quotes.json").read_text(encoding="utf-8"))
    assert q["quotes"]["2330"]["price"] == 940


def test_daily_uses_official_close(monkeypatch, tmp_path):
    from datetime import datetime
    monkeypatch.setattr(data, "price_history", lambda sid, m: make_hist(np.linspace(100, 150, 130)))
    monkeypatch.setattr(data, "fundamentals", lambda sid: {})
    monkeypatch.setattr(data, "stock_info", lambda: {})
    monkeypatch.setattr(config, "DOCS", tmp_path)
    monkeypatch.setattr(config, "load", lambda: config.Settings([config.Stock("2330")], signal_notify=False))
    today = datetime.now(daily.TPE).strftime("%Y%m%d")
    q = data.Quote("2330", "", 147.0, 150.0, None, None, None, None, "13:30:00", today, True)
    monkeypatch.setattr(data, "live_quotes", lambda pairs: {"2330": q})
    monkeypatch.setattr(holdings, "load", lambda: [holdings.Holding("2330", 1000, 100_000, None, None)])
    assert daily.main(["--no-push"]) == 0
    st = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))["stocks"][0]
    assert st["close"] == 147.0 and st["change_pct"] == -2.0
    pf = json.loads((tmp_path / "data.json").read_text(encoding="utf-8"))["portfolio"]
    assert pf["total_pl_pct"] == 47.0 and pf["rows"][0]["weight"] == 100.0 and "total_value" not in pf


def test_holdings_file_and_portfolio(tmp_path):
    p = tmp_path / "h.yml"
    p.write_text('holdings:\n  - id: "2317"\n    shares: 1000\n    cost: 200000\n    stop_loss: 180\n'
                 '  - id: "00878"\n    shares: 2000\n    avg_cost: 20\n  - id: bad\n', encoding="utf-8")
    hs = holdings.load(p)
    assert [h.id for h in hs] == ["2317", "00878"]
    assert hs[1].cost == 40000 and hs[1].stop_loss is None
    pf = holdings.portfolio(hs, {"2317": 250, "00878": 30}, {"2317": "鴻海"})
    assert pf["total_value"] == 310000 and pf["total_pl"] == 70000
    assert pf["rows"][0]["id"] == "2317" and pf["rows"][0]["weight"] == 80.6
    assert pf["warnings"] and "鴻海" in pf["warnings"][0]
    assert "合計 310,000" in holdings.summary_text(pf)


def test_check_stops_dedupe_and_shared_state():
    hs = [holdings.Holding("2330", 1000, 1_000_000, 950, 1100)]
    state = {}
    alerts.check([config.Stock("2330", below=960)], {"2330": _q(940)}, {}, state, "20261112")
    m = holdings.check_stops(hs, {"2330": _q(940)}, {}, state, "20261112")
    assert len(m) == 1 and "停損" in m[0]
    assert set(state["20261112"]) == {"2330:below", "H:2330:stop"}
    assert holdings.check_stops(hs, {"2330": _q(930)}, {}, state, "20261112") == []
    # 價格提醒再跑一次不會把停損紀錄洗掉
    alerts.check([config.Stock("2330", below=960)], {"2330": _q(930)}, {}, state, "20261112")
    assert "H:2330:stop" in state["20261112"]


def test_holdings_not_committed():
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "holdings.yml"], capture_output=True, text=True, cwd=config.ROOT).stdout
    assert tracked.strip() == "", "holdings.yml 不可以放進公開 repo"


def test_holdings_from_secret_env(monkeypatch):
    monkeypatch.setenv("HOLDINGS", 'holdings:\n  - id: "2317"\n    shares: 100\n    cost: 20000\n')
    hs = holdings.load()
    assert len(hs) == 1 and hs[0].avg_cost == 200
    monkeypatch.setenv("HOLDINGS", "::: not yaml [")
    assert holdings.load() == []


def test_public_view_hides_amounts():
    hs = [holdings.Holding("2317", 1000, 200000, 180, 300)]
    pv = holdings.public_view(holdings.portfolio(hs, {"2317": 250}, {"2317": "鴻海"}))
    text = json.dumps(pv)
    for leak in ("shares", "cost", "value", "avg_cost", "stop_loss", "200000", "250000"):
        assert leak not in text
    assert pv["rows"][0]["pl_pct"] == 25.0 and pv["total_pl_pct"] == 25.0


def test_etf_skips_technical_signal(monkeypatch):
    monkeypatch.setattr(data, "price_history", lambda sid, m: make_hist(np.linspace(40, 30, 130)))
    monkeypatch.setattr(data, "dividends", lambda sid, m: {"ttm_dps": 3.0, "last_dps": 1.0, "last_ex": "2026-08-18", "count": 4})
    st = config.Stock("00878", next_ex="2026-11-17", next_dps=1.25, yield_above=9)
    it = daily.build_stock(st, {}, {"00878": {"nav": 29.8, "premium_pct": 0.67}})
    sig = it["signal"]
    assert sig["label"] == "ETF" and sig["events"] == [] and sig["etf"]["yield"] == 10.0
    assert any("2026-11-17" in r for r in sig["reasons"]) and any("+0.67%" in r for r in sig["reasons"])
    assert not any("空頭" in r for r in sig["reasons"])


def test_yield_alert():
    stocks = [config.Stock("00878", yield_above=9)]
    q = data.Quote("00878", "國泰永續高股息", 30.0, 30.2, None, None, None, None, "10:00:00", "20261112", True)
    m = alerts.check(stocks, {"00878": q}, {}, {}, "20261112", {"00878": 3.0})
    assert len(m) == 1 and "殖利率 10.0%" in m[0]
    assert alerts.check(stocks, {"00878": q}, {}, {}, "20261112", {"00878": 2.0}) == []


# ---- 交易紀錄提醒 ----
def _encrypt_vault(obj, password, iterations=1000):
    import base64, os as _os
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt, iv = _os.urandom(16), _os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, json.dumps(obj).encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": iterations, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def test_journal_decrypt_and_reminders(tmp_path, monkeypatch):
    from datetime import date
    from stockapp import journal
    trades = [
        {"sid": "2317", "name": "鴻海", "thesis": "AI 伺服器出貨放量", "wrongIf": "跌破季線", "stop": 205,
         "nextReview": "2026-11-10", "fills": [{"date": "2026-10-01", "side": "買", "shares": 1000, "price": 220}]},
        {"sid": "00878", "name": "國泰永續高股息", "nextReview": "2026-12-30",
         "fills": [{"date": "2026-03-01", "side": "買", "shares": 1000, "price": 20}]},
        {"sid": "2330", "name": "台積電", "nextReview": "2026-11-01", "stop": 900,   # 已結案：不提醒
         "fills": [{"date": "2026-09-01", "side": "買", "shares": 1000, "price": 1000},
                   {"date": "2026-10-01", "side": "賣", "shares": 1000, "price": 950}]},
    ]
    p = tmp_path / "vault.json"
    p.write_text(json.dumps(_encrypt_vault({"trades": trades}, "pw123456")), encoding="utf-8")
    monkeypatch.setenv("JOURNAL_PASS", "wrong")
    assert journal.load(p) is None
    monkeypatch.setenv("JOURNAL_PASS", "pw123456")
    jd = journal.load(p)
    assert journal.to_watch(jd) == ["2317"]
    msg = journal.reminders(jd, {"2317": 200.0, "00878": 21.0}, date(2026, 11, 12))
    assert "2317 鴻海 現價 200 ≤ 停損 205" in msg
    assert "2317 鴻海（持有 42 天）" in msg
    assert "00878" not in msg and "2330" not in msg
    assert journal.reminders(jd, {"2317": 230.0}, date(2026, 11, 1)) == ""
