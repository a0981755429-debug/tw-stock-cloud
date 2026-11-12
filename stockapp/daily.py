"""盤後更新（每個交易日 17:30 左右跑）：抓日 K、算訊號、抓財報 → docs/data.json，並推播今日訊號摘要。

本機測試：python -m stockapp.daily --no-push
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from stockapp import config, data, notify
from stockapp.indicators import sma
from stockapp.signals import analyze

log = logging.getLogger("daily")
TPE = timezone(timedelta(hours=8))


def _round(x, n=2):
    return None if x is None or pd.isna(x) else round(float(x), n)


def build_stock(stock: config.Stock, info: dict) -> dict:
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
        "alert": {"above": stock.above, "below": stock.below, "change_pct": stock.change_pct},
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
    try:
        item["fundamentals"] = data.fundamentals(stock.id)
    except Exception as e:  # noqa: BLE001
        log.warning("%s 財報抓取失敗: %s", stock.id, e)
        item["fundamentals"] = {}
    return item


def summary_message(items: list[dict], today: str) -> str:
    lines = [f"📊 {today} 盤後自選股訊號"]
    for it in sorted(items, key=lambda x: -x["signal"]["score"]):
        chg = it.get("change_pct")
        chg_s = f"{chg:+.2f}%" if chg is not None else "—"
        close = it.get("close")
        lines.append(f"\n{it['id']} {it['name']}  {close if close is not None else '—'}（{chg_s}）"
                     f"  {it['signal']['label']} {it['signal']['score']:+d}")
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
    items = []
    for s in settings.stocks:
        log.info("處理 %s", s.id)
        try:
            items.append(build_stock(s, info))
        except Exception as e:  # noqa: BLE001
            log.exception("%s 失敗: %s", s.id, e)
    config.DOCS.mkdir(exist_ok=True)
    out = {"updated_at": now.strftime("%Y-%m-%d %H:%M"), "stocks": items}
    (config.DOCS / "data.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log.info("已寫入 docs/data.json（%d 檔）", len(items))

    if not args.no_push and settings.signal_notify and items:
        notify.telegram(summary_message(items, now.strftime("%m/%d")))
    ok = sum(1 for it in items if it["close"] is not None)
    if settings.stocks and ok == 0:
        log.error("所有股票都抓不到價格，可能資料來源暫時故障")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
