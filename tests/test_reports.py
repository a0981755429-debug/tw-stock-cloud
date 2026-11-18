from datetime import date, datetime

from stockapp import data, reports


def _d():
    v_hold = {"score": 30, "level": 1, "label": "偏多", "action": "續抱，可小量加碼", "held": True,
              "buy": [{"factor": "三大法人", "pts": 10, "text": ""}], "sell": [], "info": [], "missing": []}
    v_etf = {"score": 26, "level": 1, "label": "偏多", "action": "續抱，可小量加碼", "held": True,
             "buy": [], "sell": [], "info": [], "missing": [], "yield_dps": 3.3}
    v_bad = {"score": -20, "level": -1, "label": "偏空", "action": "暫不進場", "held": False,
             "buy": [], "sell": [], "info": [], "missing": []}
    return {"updated_at": "2026-11-16 18:00",
            "portfolio": {"rows": [{"id": "2317", "name": "鴻海", "weight": 40}, {"id": "00878", "name": "國泰永續高股息", "weight": 60}]},
            "stocks": [
                {"id": "2317", "name": "鴻海", "market": "上市", "close": 218.5, "change_pct": -9.0,
                 "alert": {"below": 228}, "signal": {}, "verdict": v_hold},
                {"id": "00878", "name": "國泰永續高股息", "market": "上市", "close": 32.4, "change_pct": -3.0,
                 "alert": {"yield_above": 9}, "dividends": [{"ex": "2026-11-17", "dps": 1.25}],
                 "signal": {"etf": {"ttm_dps": 2.5, "next_ex": "2026-11-17", "next_dps": 1.25}}, "verdict": v_etf},
                {"id": "1314", "name": "中石化", "market": "上市", "close": 7.2, "change_pct": 1.0,
                 "alert": {}, "signal": {}, "verdict": v_bad},
            ]}


def test_premarket_message():
    us = {"^SOX": {"close": 7000, "chg": -3.0}, "TSM": {"close": 280.0, "chg": -4.0}, "TWD=X": {"close": 32.0, "chg": 0.2}}
    msg = reports.premarket_message(_d(), us, date(2026, 11, 17))
    assert "費城半導體" in msg and "費半大跌" in msg
    assert "國泰永續高股息 今天 除息 1.25 元（持股）" in msg
    assert "鴻海 218.50，已到你設的買點 228" in msg
    assert "自選偏空：中石化" in msg and "不是投資建議" in msg


def _q(sid, price, prev, day="20261117"):
    return data.Quote(sid, "", price, prev, None, None, None, None, "12:00:00", day, True)


def test_midday_message_flags_dip_yield_and_ex_dividend():
    qs = {"2317": _q("2317", 212.0, 218.5), "00878": _q("00878", 31.2, 32.4), "1314": _q("1314", 7.3, 7.2),
          "t00": _q("t00", 40500, 41040)}
    msg = reports.midday_message(_d(), qs, datetime(2026, 11, 17, 12, 5))
    assert "加權指數 40,500" in msg
    assert "鴻海 212.00" in msg and "今天回檔" in msg
    assert "今天除息，跌幅含配息" in msg          # 00878 除息日不要說成回檔
    assert "殖利率 10.6% ≥ 你設的 9%" in msg


def test_postmarket_message():
    d = _d()
    prev = {"stocks": [{"id": "2317", "verdict": {"score": 50, "buy": [{"factor": "三大法人", "pts": 30}], "sell": []}}]}
    msg = reports.postmarket_message(d["stocks"], prev, d["portfolio"], {"close": 41040, "pts": -2635, "chg": -6.0},
                                     date(2026, 11, 16))
    assert "加權指數 41,040（-2,635 點" in msg
    assert "你的持股今天約 -5.40%" in msg
    assert "鴻海 +50 → +30" in msg and "三大法人 -20" in msg
    assert "國泰永續高股息 明天 除息" in msg


def test_holiday_weekend_and_fail_open(monkeypatch):
    assert reports.is_holiday(date(2026, 11, 15))          # 週日
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(reports.requests, "get", boom)
    assert reports.is_holiday(date(2026, 11, 16)) is False  # 查不到就照發


def test_holiday_parses_twse_roc_dates(monkeypatch):
    rows = [{"Name": "中華民國開國紀念日", "Date": "1160101", "Weekday": "五", "Description": "依規定放假1日。"},
            {"Name": "國曆新年開始交易日", "Date": "1160104", "Weekday": "一", "Description": "開始交易。"}]

    class R:
        def json(self):
            return rows
    monkeypatch.setattr(reports.requests, "get", lambda *a, **k: R())
    assert reports.is_holiday(date(2027, 1, 1))
    assert not reports.is_holiday(date(2027, 1, 4))
    assert not reports.is_holiday(date(2027, 1, 5))


def test_window_and_once_per_day(tmp_path):
    from datetime import timedelta, timezone
    tpe = timezone(timedelta(hours=8))
    tue = datetime(2026, 11, 17, 8, 25, tzinfo=tpe)
    assert reports.in_window("premarket", tue)
    assert not reports.in_window("midday", tue)
    assert reports.in_window("midday", tue.replace(hour=12, minute=45))
    assert not reports.in_window("midday", tue.replace(hour=15))          # 排程拖太晚就不發
    assert not reports.in_window("premarket", datetime(2026, 11, 21, 8, 5, tzinfo=tpe))  # 週六
    f = tmp_path / "sent.json"
    assert not reports.already_sent("midday", tue.date(), f)
    reports.mark_sent("midday", tue.date(), f)
    assert reports.already_sent("midday", tue.date(), f)
    assert not reports.already_sent("premarket", tue.date(), f)
    assert not reports.already_sent("midday", date(2026, 11, 18), f)
