"""盤後更新（每個交易日 17:30 左右跑）：抓日 K、算訊號、抓財報 → docs/data.json，並推播今日訊號摘要。

本機測試：python -m stockapp.daily --no-push
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from stockapp import config, data, holdings, notify
from stockapp.indicators import sma
from stockapp.signals import analyze

log = logging.getLogger("daily")
TPE = timezone(timedelta(hours=8))


def _round(x, n=2):
    return None if x is None or pd.isna(x) else round(float(x), n)


def etf_signal(stock: config.Stock, close: float | None, div: dict, prem: dict | None) -> dict:
    """ETF 不打技術分數：改看殖利率、配息、除息日、折溢價。"""
    reasons = []
    y = None
    if div.get("ttm_dps") and close:
        y = div["ttm_dps"] / close * 100
        reasons.append(f"近一年配息 {div['ttm_dps']:g} 元（{div['count']} 次），以現價計殖利率約 {y:.1f}%")
    if div.get("last_ex"):
        reasons.append(f"最近一次除息 {div['last_ex']}，每股 {div['last_dps']:g} 元")
    if stock.next_ex:
        reasons.append(f"下次除息 {stock.next_ex}" + (f"，預估每股 {stock.next_dps:g} 元" if stock.next_dps else ""))
    if prem and prem.get("premium_pct") is not None:
        p = prem["premium_pct"]
        tag = "溢價偏高，不宜追買" if p > 1 else "折價，相對便宜" if p < -0.5 else "接近淨值"
        reasons.append(f"市價相對淨值 {p:+.2f}%（{tag}）")
    reasons.append("高股息 ETF 以長期領息為主，不使用技術訊號與停損")
    return {"score": 0, "label": "ETF", "reasons": reasons, "events": [], "indicators": {},
            "etf": {"yield": round(y, 2) if y else None, **div,
                    "next_ex": stock.next_ex or None, "next_dps": stock.next_dps,
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
    if data.is_etf(stock.id):
        try:
            div = data.dividends(stock.id, market)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 配息抓取失敗: %s", stock.id, e)
            div = {}
        item["signal"] = etf_signal(stock, item["close"], div, (premiums or {}).get(stock.id))
        item["fundamentals"] = {}
        return item
    try:
        item["fundamentals"] = data.fundamentals(stock.id)
    except Exception as e:  # noqa: BLE001
        log.warning("%s 財報抓取失敗: %s", stock.id, e)
        item["fundamentals"] = {}
    return item


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
            if q and q.is_live and q.date == now.strftime("%Y%m%d"):
                it["close"] = round(q.price, 2)
                it["change_pct"] = _round(q.change_pct)
                it["date"] = now.strftime("%Y-%m-%d")
    except Exception as e:  # noqa: BLE001
        log.warning("官方收盤價校正失敗：%s", e)

    # 持股損益
    pf = None
    hs = holdings.load()
    if hs:
        prices = {it["id"]: it["close"] for it in items if it["close"] is not None}
        names = {sid: v["name"] for sid, v in info.items()}
        names.update({it["id"]: it["name"] for it in items})
        missing = [h.id for h in hs if h.id not in prices]
        if missing:
            qs = data.live_quotes([(sid, info.get(sid, {}).get("market", "twse")) for sid in missing])
            prices.update({sid: q.price for sid, q in qs.items()})
        pf = holdings.portfolio(hs, prices, names)

    config.DOCS.mkdir(exist_ok=True)
    out = {"updated_at": now.strftime("%Y-%m-%d %H:%M"), "stocks": items,
           "portfolio": holdings.public_view(pf) if pf else None}
    (config.DOCS / "data.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log.info("已寫入 docs/data.json（%d 檔）", len(items))

    if not args.no_push and settings.signal_notify and items:
        msg = summary_message(items, now.strftime("%m/%d"))
        if pf:
            msg += "\n\n" + holdings.summary_text(pf)
        notify.telegram(msg)
    ok = sum(1 for it in items if it["close"] is not None)
    if settings.stocks and ok == 0:
        log.error("所有股票都抓不到價格，可能資料來源暫時故障")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
