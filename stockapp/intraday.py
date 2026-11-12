"""盤中檢查（交易時段每 10 分鐘左右跑）：抓即時價 → 判斷價格提醒 → Telegram，並寫 docs/quotes.json。

本機測試：python -m stockapp.intraday --force --no-push
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, time, timedelta, timezone

from stockapp import alerts, config, data, holdings, notify

log = logging.getLogger("intraday")
TPE = timezone(timedelta(hours=8))
OPEN, CLOSE = time(9, 0), time(13, 40)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="不管時段都跑")
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    now = datetime.now(TPE)
    if not args.force and not (now.weekday() < 5 and OPEN <= now.time() <= CLOSE):
        log.info("非交易時段（%s），結束", now.strftime("%a %H:%M"))
        return 0

    settings = config.load()
    hs = holdings.load()
    ids = list(dict.fromkeys([s.id for s in settings.stocks] + [h.id for h in hs]))
    # 名稱 / 上市櫃別：優先用盤後產生的 data.json，省一次 FinMind 呼叫
    names, markets = {}, {}
    try:
        cached = json.loads((config.DOCS / "data.json").read_text(encoding="utf-8"))
        for it in cached.get("stocks", []):
            names[it["id"]] = it["name"]
            markets[it["id"]] = "tpex" if it.get("market") == "上櫃" else "twse"
    except (FileNotFoundError, ValueError):
        pass
    missing = [sid for sid in ids if sid not in markets]
    if missing:
        info = data.stock_info()
        for sid in missing:
            names[sid] = info.get(sid, {}).get("name", sid)
            markets[sid] = info.get(sid, {}).get("market", "twse")

    quotes = data.live_quotes([(sid, markets[sid]) for sid in ids])
    today = now.strftime("%Y%m%d")
    # 交易所資料日期不是今天 → 今天休市（國定假日），不發提醒
    if not args.force and quotes and all(q.date and q.date != today for q in quotes.values()):
        log.info("今天休市，結束")
        return 0

    state = config.load_state()
    msgs = alerts.check(settings.stocks, quotes, names, state, today)
    msgs += holdings.check_stops(hs, quotes, names, state, today)
    if msgs and not args.no_push:
        notify.telegram("🔔 到價提醒\n\n" + "\n".join(msgs))
    if not args.no_push:
        config.save_state(state)
    for m in msgs:
        log.info(m)

    out = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M"),
        "quotes": {sid: {"price": q.price, "change_pct": None if q.change_pct is None else round(q.change_pct, 2),
                         "time": q.time, "live": q.is_live} for sid, q in quotes.items()},
    }
    (config.DOCS / "quotes.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log.info("報價 %d 檔、提醒 %d 則", len(quotes), len(msgs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
