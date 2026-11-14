"""盤後更新（每個交易日 17:30 左右跑）：抓日 K、算訊號、抓財報 → docs/data.json，並推播今日訊號摘要。

本機測試：python -m stockapp.daily --no-push
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from stockapp import broker, config, data, holdings, insights, journal, notify
from stockapp.indicators import sma
from stockapp.signals import analyze

log = logging.getLogger("daily")
TPE = timezone(timedelta(hours=8))


def _round(x, n=2):
    return None if x is None or pd.isna(x) else round(float(x), n)


def etf_signal(stock: config.Stock, close: float | None, div: dict, prem: dict | None,
               nxt: dict | None = None) -> dict:
    """ETF 不打技術分數：改看殖利率、配息、除息日、折溢價。

    nxt = FinMind 股利政策表抓到的「已公告、還沒除息」那一次；有的話優先，沒有才用 watchlist.yml 手填的。
    """
    reasons = []
    next_ex, next_dps, src = stock.next_ex or None, stock.next_dps, "手填" if stock.next_ex else None
    if nxt:
        next_ex, next_dps, src = nxt["ex"], nxt["dps"], "自動"
    y = None
    if div.get("ttm_dps") and close:
        y = div["ttm_dps"] / close * 100
        reasons.append(f"近一年配息 {div['ttm_dps']:g} 元（{div['count']} 次），以現價計殖利率約 {y:.1f}%")
    if div.get("last_ex"):
        reasons.append(f"最近一次除息 {div['last_ex']}，每股 {div['last_dps']:g} 元")
    if next_ex:
        reasons.append(f"下次除息 {next_ex}" + (f"，每股 {next_dps:g} 元" if next_dps else "")
                       + ("（投信已公告）" if src == "自動" else "（手填）"))
    if prem and prem.get("premium_pct") is not None:
        p = prem["premium_pct"]
        tag = "溢價偏高，不宜追買" if p > 1 else "折價，相對便宜" if p < -0.5 else "接近淨值"
        reasons.append(f"市價相對淨值 {p:+.2f}%（{tag}）")
    reasons.append("高股息 ETF 以長期領息為主，不使用技術訊號與停損")
    return {"score": 0, "label": "ETF", "reasons": reasons, "events": [], "indicators": {},
            "etf": {"yield": round(y, 2) if y else None, **div,
                    "next_ex": next_ex, "next_dps": next_dps, "next_src": src,
                    "premium_pct": (prem or {}).get("premium_pct"), "nav": (prem or {}).get("nav")}}


def build_stock(stock: config.Stock, info: dict, premiums: dict | None = None) -> dict:
    meta = info.get(stock.id, {})
    market = meta.get("market", "twse")
    hist = data.price_history(stock.id, market)
    sig = analyze(hist)
    item = {
        "id": stock.id,
        "name": meta.get("name", stock.id),
        "market": "上市" if market == "twse" else "上櫃",
        "industry": meta.get("industry", ""),
        "note": stock.note,
        "alert": {"above": stock.above, "below": stock.below, "change_pct": stock.change_pct,
                  "yield_above": stock.yield_above},
        "signal": {"score": sig.score, "label": sig.label, "reasons": sig.reasons,
                   "events": sig.events, "indicators": sig.indicators},
        "close": None, "change_pct": None, "date": None, "chart": [],
    }
    if not hist.empty:
        close = hist["close"]
        item["close"] = _round(close.iloc[-1])
        item["date"] = hist.index[-1].strftime("%Y-%m-%d")
        if len(close) >= 2:
            item["change_pct"] = _round((close.iloc[-1] / close.iloc[-2] - 1) * 100)
        ma20, ma60 = sma(close, 20), sma(close, 60)
        tail = hist.tail(120)
        item["chart"] = [
            [d.strftime("%Y-%m-%d"), _round(c), _round(ma20.loc[d]), _round(ma60.loc[d]), int(v or 0)]
            for d, c, v in zip(tail.index, tail["close"], tail["volume"])
        ]
        item["high_52w"] = _round(close.tail(250).max())
        item["low_52w"] = _round(close.tail(250).min())
    today = date.today()
    events = _safe(lambda: data.dividend_events(stock.id), [], f"{stock.id} 配息行事曆")
    item["dividends"] = events[-8:]
    item["_div_all"] = events          # 完整清單給持股算含息報酬用，不寫進 data.json
    nxt = insights.next_dividend(events, today)
    if data.is_etf(stock.id):
        div = _safe(lambda: data.dividends(stock.id, market), {}, f"{stock.id} 配息")
        if not div.get("ttm_dps") and events:     # Yahoo 抓不到時改用 FinMind 除息紀錄
            past = [e for e in events if e["ex"] <= today.isoformat()]
            if past:
                div = {"ttm_dps": insights.ttm_dps(events, today), "last_dps": past[-1]["dps"],
                       "last_ex": past[-1]["ex"],
                       "count": sum(1 for e in past if e["ex"] > (today - timedelta(days=365)).isoformat())}
        item["signal"] = etf_signal(stock, item["close"], div, (premiums or {}).get(stock.id), nxt)
        item["fundamentals"] = {}
        return item
    item["fundamentals"] = _safe(lambda: data.fundamentals(stock.id), {}, f"{stock.id} 財報")
    item["fundamentals"]["next_dividend"] = nxt
    item["fundamentals"]["health"] = _safe(lambda: data.health(stock.id), {}, f"{stock.id} 財務體質")
    item["chips"] = _safe(lambda: data.chips(stock.id), {}, f"{stock.id} 籌碼")
    _add_insights(item)
    return item


def _safe(fn, default, what: str):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("%s 抓取失敗: %s", what, e)
        return default


def _add_insights(item: dict) -> None:
    """把籌碼 / 財務體質整理成白話理由；「今天剛發生」的（法人連買 3/5/10 天、融資剛過熱）也列入推播事件。"""
    ch, h = item.get("chips") or {}, item["fundamentals"].get("health") or {}
    sig = item["signal"]
    events = (ch.get("institutional") or {}).get("events", []) + (ch.get("margin") or {}).get("events", [])
    item["chip_reasons"] = (insights.institutional_reasons(ch.get("institutional"))
                            + insights.margin_reasons(ch.get("margin"))
                            + insights.foreign_reasons(ch.get("foreign")))
    item["health_reasons"] = insights.health_reasons(h.get("cash_flow"), h.get("balance"))
    nxt = item["fundamentals"].get("next_dividend")
    if nxt:
        sig["reasons"].append(f"下次除息 {nxt['ex']}，每股現金股利 {nxt['dps']:g} 元（已公告）")
    for ev in events:
        sig["events"].append(ev)
        sig["reasons"].append(ev)


def summary_message(items: list[dict], today: str) -> str:
    lines = [f"📊 {today} 盤後自選股訊號"]
    for it in sorted(items, key=lambda x: (x["signal"]["label"] == "ETF", -x["signal"]["score"])):
        chg = it.get("change_pct")
        chg_s = f"{chg:+.2f}%" if chg is not None else "—"
        close = it.get("close")
        etf = it["signal"].get("etf")
        tag = (f"殖利率 {etf['yield']:.1f}%" if etf and etf.get("yield") else "ETF") if etf is not None \
            else f"{it['signal']['label']} {it['signal']['score']:+d}"
        lines.append(f"\n{it['id']} {it['name']}  {close if close is not None else '—'}（{chg_s}）  {tag}")
        for ev in it["signal"]["events"]:
            lines.append(f"  • {ev}")
    lines.append("\n⚠️ 訊號僅供參考，非投資建議")
    return "\n".join(lines)


def journal_text(items: list[dict], info: dict, now: datetime) -> str:
    """交易紀錄的檢討 / 停損提醒（需要 JOURNAL_PASS）。失敗不影響其他推播。"""
    try:
        jd = journal.load()
        if not jd:
            return ""
        prices = {it["id"]: it["close"] for it in items if it["close"] is not None}
        missing = [sid for sid in journal.to_watch(jd) if sid not in prices]
        if missing:
            qs = data.live_quotes([(sid, info.get(sid, {}).get("market", "twse")) for sid in missing])
            prices.update({sid: q.price for sid, q in qs.items()})
        return journal.reminders(jd, prices, now.date())
    except Exception as e:  # noqa: BLE001
        log.warning("交易紀錄提醒失敗：%s", type(e).__name__)
        return ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = config.load()
    info = data.stock_info()
    now = datetime.now(TPE)
    premiums = data.etf_premiums() if any(data.is_etf(s.id) for s in settings.stocks) else {}
    log.info("ETF 折溢價資料 %d 檔", len(premiums))
    items = []
    for s in settings.stocks:
        log.info("處理 %s", s.id)
        try:
            items.append(build_stock(s, info, premiums))
        except Exception as e:  # noqa: BLE001
            log.exception("%s 失敗: %s", s.id, e)
    # 收盤價 / 漲跌幅以證交所官方（mis）為準；Yahoo 的昨收偶爾會跟官方不同
    try:
        mk = {it["id"]: ("tpex" if it["market"] == "上櫃" else "twse") for it in items}
        quotes = data.live_quotes(list(mk.items()))
        for it in items:
            q = quotes.get(it["id"])
            # 交易所資料日期不早於日 K 最後一天就採用（週末重跑、或 Yahoo 的 ETF 資料晚一天時，仍是最新官方收盤）
            qd = f"{q.date[:4]}-{q.date[4:6]}-{q.date[6:8]}" if q and len(q.date or "") == 8 else None
            if q and q.is_live and qd and qd >= (it["date"] or ""):
                it["close"] = round(q.price, 2)
                it["change_pct"] = _round(q.change_pct)
                if it["chart"] and qd > it["chart"][-1][0]:
                    it["chart"].append([qd, it["close"], None, None, int((q.volume_lots or 0) * 1000)])
                it["date"] = qd
    except Exception as e:  # noqa: BLE001
        log.warning("官方收盤價校正失敗：%s", e)

    # 持股損益
    pf = None
    hs, broker_notes = broker.sync(holdings.load())
    if hs:
        prices = {it["id"]: it["close"] for it in items if it["close"] is not None}
        names = {sid: v["name"] for sid, v in info.items()}
        names.update({it["id"]: it["name"] for it in items})
        missing = [h.id for h in hs if h.id not in prices]
        if missing:
            qs = data.live_quotes([(sid, info.get(sid, {}).get("market", "twse")) for sid in missing])
            prices.update({sid: q.price for sid, q in qs.items()})
        div_events = {it["id"]: it.get("_div_all") or [] for it in items}
        for h in hs:
            if h.id not in div_events:
                div_events[h.id] = _safe(lambda: data.dividend_events(h.id), [], f"{h.id} 配息行事曆")
        pf = holdings.portfolio(hs, prices, names, div_events, now.date())

    config.DOCS.mkdir(exist_ok=True)
    public_items = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
    out = {"updated_at": now.strftime("%Y-%m-%d %H:%M"), "stocks": public_items,
           "portfolio": holdings.public_view(pf) if pf else None}
    (config.DOCS / "data.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log.info("已寫入 docs/data.json（%d 檔）", len(items))

    if not args.no_push and settings.signal_notify and items:
        msg = summary_message(items, now.strftime("%m/%d"))
        if pf:
            msg += "\n\n" + holdings.summary_text(pf)
        if broker_notes:
            msg += "\n\n🏦 永豐庫存同步\n" + "\n".join(broker_notes)
        dr = holdings.dividend_reminders(pf, items, now.date())
        if dr:
            msg += "\n\n" + dr
        jr = journal_text(items, info, now)
        if jr:
            msg += "\n\n" + jr
        notify.telegram(msg)
    ok = sum(1 for it in items if it["close"] is not None)
    if settings.stocks and ok == 0:
        log.error("所有股票都抓不到價格，可能資料來源暫時故障")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
