"""價格提醒判斷。同一檔、同一種提醒，每個交易日只通知一次。"""
from __future__ import annotations

from stockapp.config import Stock
from stockapp.data import Quote


def check(stocks: list[Stock], quotes: dict[str, Quote], names: dict[str, str],
          state: dict, today: str) -> list[str]:
    """回傳要推播的訊息列表，並就地更新 state（{today: [key, ...]}）。"""
    sent_today = set(state.get(today, []))
    msgs = []
    for s in stocks:
        q = quotes.get(s.id)
        if q is None or not q.is_live:
            continue
        name = names.get(s.id) or q.name or s.id
        checks = []
        if s.above is not None and q.price >= s.above:
            checks.append(("above", f"📈 {s.id} {name} 漲到 {q.price:g}（你設定 ≥ {s.above:g}）"))
        if s.below is not None and q.price <= s.below:
            checks.append(("below", f"📉 {s.id} {name} 跌到 {q.price:g}（你設定 ≤ {s.below:g}）"))
        chg = q.change_pct
        if s.change_pct is not None and chg is not None and abs(chg) >= s.change_pct:
            arrow = "🔺" if chg > 0 else "🔻"
            checks.append(("pct", f"{arrow} {s.id} {name} 今日{'上漲' if chg > 0 else '下跌'} {abs(chg):.1f}%（現價 {q.price:g}）"))
        for kind, msg in checks:
            key = f"{s.id}:{kind}"
            if key in sent_today:
                continue
            sent_today.add(key)
            msgs.append(msg + (f"\n  備註：{s.note}" if s.note else ""))
    # 只保留今天的紀錄，舊的清掉
    state.clear()
    if sent_today:
        state[today] = sorted(sent_today)
    return msgs
