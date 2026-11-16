from datetime import date

from stockapp import daily, verdict

TODAY = date(2026, 11, 16)


def _stock(**kw):
    it = {"id": "2317", "name": "鴻海", "close": 200.0, "industry": "其他電子業",
          "alert": {"below": 228, "above": 258},
          "signal": {"score": 40, "label": "偏多", "reasons": ["股價在月線、季線之上，且月線高於季線（多頭排列）"], "events": []},
          "chips": {"institutional": {"foreign": {"sum5": 1000, "streak": 3}, "trust": {"sum5": 200, "streak": 1}},
                    "margin": {"hot": False, "change_20d_pct": -12.0},
                    "foreign": {"ratio": 40.0, "change_20d": 0.6}},
          "fundamentals": {"revenue": [{"yoy": 20}, {"yoy": 30}, {"yoy": 25}], "per": 15,
                           "eps": [{"eps": 2}] * 4 + [{"eps": 3}] * 4, "health": {}}}
    it.update(kw)
    return it


def test_bullish_stock_lists_buy_reasons_and_action_depends_on_holding():
    a = {"consensus": {"mean": 260, "n": 20, "key": "買進"}, "summary": {"bull": 5, "bear": 0}}
    v = verdict.stock(_stock(), a, None, TODAY)
    assert v["level"] == 2 and v["action"] == "可考慮分批買進"
    factors = {x["factor"] for x in v["buy"]}
    assert {"技術面", "三大法人", "分析師目標價", "月營收", "EPS", "你設的買點", "融資"} <= factors
    held = verdict.stock(_stock(), a, {"weight": 20, "pl_pct": -5}, TODAY)
    assert held["action"] == "可考慮加碼" and held["held"]


def test_bearish_stock_and_concentration():
    it = _stock(close=300.0, signal={"score": -60, "label": "偏空", "reasons": [], "events": []},
                chips={"margin": {"hot": True, "change_20d_pct": 20.0}},
                fundamentals={"revenue": [{"yoy": -20}, {"yoy": -15}], "per": 50, "health": {}})
    a = {"consensus": {"mean": 250, "n": 10}, "mops": [{"date": "2026-11-14", "subject": "公告處分資產"}]}
    v = verdict.stock(it, a, {"weight": 45}, TODAY)
    assert v["level"] == -2 and v["action"] == "可考慮減碼"
    sell = {x["factor"] for x in v["sell"]}
    assert {"技術面", "融資", "分析師目標價", "月營收", "本益比", "你設的賣點", "重大訊息", "持股集中度"} <= sell
    assert "三大法人" in v["missing"]


def test_etf_uses_yield_threshold_and_total_return_notes():
    it = {"id": "00878", "name": "國泰永續高股息", "close": 32.0, "alert": {"yield_above": 9},
          "signal": {"etf": {"yield": 7.6, "premium_pct": 0.2, "next_ex": "2026-11-17"}}}
    a = {"yields": {"recent_dps": 3.34, "freq": 4, "recent_incl_next": True, "annualized_pct": 15.6}}
    v = verdict.etf(it, {"score": -20, "label": "中性", "reasons": [], "events": []}, a, {"weight": 50}, TODAY)
    assert v["buy"][0]["factor"] == "殖利率門檻" and v["buy"][0]["pts"] == 30
    assert v["level"] == 1 and v["action"] == "續抱，可小量加碼"
    assert any("年化" in x for x in v["info"]) and any("除息" in x for x in v["info"])
    far = verdict.etf({**it, "close": 60.0}, None, a, None, TODAY)
    assert far["sell"][0]["factor"] == "殖利率門檻"


def test_add_verdicts_and_message():
    items = [_stock(), {"id": "00878", "name": "國泰永續高股息", "close": 32.0, "alert": {"yield_above": 9},
                        "signal": {"etf": {"yield": 9.5}}, "_tech": None}]
    daily.add_verdicts(items, {"rows": [{"id": "00878", "weight": 50}]}, TODAY, analyst={}, fresh_mops={})
    assert all("verdict" in it for it in items)
    msg = verdict.summary_message(items)
    assert "【持股】" in msg and "00878" in msg and "不是投資建議" in msg
