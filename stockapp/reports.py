"""定時分析推播：盤前（08:30）、盤中買賣訊號（12:30）、盤後分析段落（併在 17:30 盤後推播最前面）。

都只讀現成的 docs/data.json（前一次盤後的綜合訊號）再加上即時資料，不重算全部指標，跑得快。
結構：fetch_* 連網抓資料；*_message 是純函式（方便測試）；main 給 GitHub Actions 呼叫。

本機測試：
  python -m stockapp.reports premarket --no-push
  python -m stockapp.reports midday --force --no-push
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

import requests

from stockapp import config, data, notify
from stockapp.verdict import DISCLAIMER

log = logging.getLogger("reports")
TPE = timezone(timedelta(hours=8))
INDEX_ID = "t00"          # mis 的加權指數代號
US = [("^SOX", "費城半導體"), ("^IXIC", "那斯達克"), ("^GSPC", "S&P 500"), ("NVDA", "輝達"), ("TSM", "台積電 ADR")]
ADR_RATIO = {"TSM": ("2330", 5)}   # 1 股 ADR = 5 股台股
HOLIDAY_API = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
SENT = config.ROOT / "state" / "reports_sent.json"   # 每份報告每天只發一次（排程有多個備援時間）
WINDOW = {"premarket": (time(6, 30), time(9, 0)), "midday": (time(9, 0), time(13, 35))}


def already_sent(kind: str, day: date, path=SENT) -> bool:
    return config.load_state(path).get(kind) == day.isoformat()


def mark_sent(kind: str, day: date, path=SENT) -> None:
    st = config.load_state(path)
    st[kind] = day.isoformat()
    config.save_state(st, path)


def in_window(kind: str, now: datetime) -> bool:
    """排程延遲太久（例如盤前分析拖到中午才跑）就不發，免得收到過時的報告。"""
    start, end = WINDOW[kind]
    return now.weekday() < 5 and start <= now.time() <= end


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def _load_data() -> dict:
    try:
        return json.loads((config.DOCS / "data.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _held(d: dict) -> dict[str, dict]:
    return {r["id"]: r for r in ((d.get("portfolio") or {}).get("rows") or [])}


# ---------------------------------------------------------------- 抓資料（連網）
def fetch_us() -> dict[str, dict]:
    """美股收盤：{代號: {"close", "chg"}}，加上 "TWD=X" 匯率。抓不到的略過。"""
    out = {}
    try:
        import yfinance as yf
    except ImportError:
        return out
    for sym in [s for s, _ in US] + ["TWD=X"]:
        try:
            h = yf.Ticker(sym).history(period="7d", interval="1d", auto_adjust=False)
            c = h["Close"].dropna()
            if len(c) >= 2:
                out[sym] = {"close": round(float(c.iloc[-1]), 2), "chg": round((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2),
                            "date": c.index[-1].strftime("%m/%d")}
        except Exception as e:  # noqa: BLE001
            log.info("yfinance %s 失敗：%s", sym, e)
    return out


def is_holiday(day: date) -> bool:
    """證交所休市日（官方 OpenAPI）；抓不到就當作有開盤（寧可多發一則）。"""
    if day.weekday() >= 5:
        return True
    try:
        r = requests.get(HOLIDAY_API, timeout=15, headers={"User-Agent": "Mozilla/5.0 (tw-stock-cloud)"})
        rows = r.json() or []
    except Exception as e:  # noqa: BLE001
        log.info("休市日查詢失敗：%s", e)
        return False
    roc = f"{day.year - 1911}{day.month:02d}{day.day:02d}"
    for row in rows:
        vals = [re.sub(r"\D", "", str(v)) for v in row.values()]
        name = " ".join(str(v) for v in row.values())
        if roc in vals or day.strftime("%Y%m%d") in vals:
            # 「開始交易 / 最後交易」那種是開盤日，不算休市
            return not any(k in name for k in ("開始交易", "最後交易", "開市"))
    return False


def index_quote(quotes: dict) -> dict | None:
    q = quotes.get(INDEX_ID)
    if q and q.is_live:
        return {"close": round(q.price, 2), "chg": None if q.change_pct is None else round(q.change_pct, 2),
                "pts": round(q.price - q.prev_close, 2) if q.prev_close else None}
    return None


# ---------------------------------------------------------------- 共用片段
def _upcoming_divs(d: dict, today: date, days: int, start: date | None = None) -> list[str]:
    """start（預設今天）起到 today + days 天內除息的持股 / 自選股。"""
    t, end = today.isoformat(), (today + timedelta(days=days)).isoformat()
    s0, tmr = (start or today).isoformat(), (today + timedelta(days=1)).isoformat()
    held = _held(d)
    out = []
    for it in d.get("stocks") or []:
        evs = list(it.get("dividends") or [])
        etf = (it.get("signal") or {}).get("etf") or {}
        if etf.get("next_ex") and etf.get("next_dps"):
            evs.append({"ex": etf["next_ex"], "dps": etf["next_dps"]})
        seen = set()
        for e in evs:
            ex = str(e.get("ex") or "")
            if len(ex) == 10 and s0 <= ex <= end and ex not in seen:
                seen.add(ex)
                when = "今天" if ex == t else "明天" if ex == tmr else ex[5:]
                out.append(f"{it['name']} {when} 除息 {e['dps']:g} 元" + ("（持股）" if it["id"] in held else "")
                           + ("：開盤價會扣掉配息，含息總報酬不變" if ex == t else ""))
    return out


def _near_alerts(d: dict, price_of) -> list[str]:
    """離你在 watchlist.yml 設的買賣點 2% 以內的。"""
    out = []
    for it in d.get("stocks") or []:
        a, p = it.get("alert") or {}, price_of(it)
        if not p:
            continue
        if a.get("below") and p <= a["below"] * 1.02:
            out.append(f"{it['name']} {p:.2f}，{'已到' if p <= a['below'] else '接近'}你設的買點 {a['below']:g}")
        if a.get("above") and p >= a["above"] * 0.98:
            out.append(f"{it['name']} {p:.2f}，{'已到' if p >= a['above'] else '接近'}你設的賣點 {a['above']:g}")
    return out


def _verdict_recap(d: dict, n: int = 3) -> list[str]:
    xs = [it for it in d.get("stocks") or [] if it.get("verdict")]
    held = [it for it in xs if it["verdict"]["held"]]
    watch = sorted((it for it in xs if not it["verdict"]["held"]), key=lambda it: -it["verdict"]["score"])
    out = []
    if held:
        out.append("持股：" + "、".join(f"{it['name']} {it['verdict']['score']:+d}（{it['verdict']['action']}）"
                                       for it in sorted(held, key=lambda it: -it["verdict"]["score"])))
    buy = [it for it in watch if it["verdict"]["level"] >= 1][:n]
    if buy:
        out.append("自選偏多：" + "、".join(f"{it['name']} {it['verdict']['score']:+d}" for it in buy))
    bad = [it for it in watch if it["verdict"]["level"] <= -1]
    if bad:
        out.append("自選偏空：" + "、".join(f"{it['name']} {it['verdict']['score']:+d}" for it in bad))
    return out


# ---------------------------------------------------------------- 盤前
def premarket_message(d: dict, us: dict, today: date) -> str:
    lines = [f"🌅 {today:%m/%d} 盤前分析"]
    if us:
        lines.append("\n【昨夜美股】")
        for sym, nm in US:
            x = us.get(sym)
            if x:
                lines.append(f"{nm} {x['close']:,.2f}（{_pct(x['chg'])}）")
        fx = us.get("TWD=X")
        if fx:
            lines.append(f"美元兌台幣 {fx['close']:.3f}（{'台幣貶' if fx['chg'] > 0 else '台幣升'} {abs(fx['chg']):.2f}%）")
        tsm, stocks = us.get("TSM"), {it["id"]: it for it in d.get("stocks") or []}
        tw = stocks.get("2330", {}).get("close")
        if tsm and fx and tw:
            prem = (tsm["close"] * fx["close"] / ADR_RATIO["TSM"][1] / tw - 1) * 100
            lines.append(f"台積電 ADR 換算台股 {tsm['close'] * fx['close'] / 5:,.0f} 元，比台股昨收 {tw:,.0f} {prem:+.1f}%")
        sox = (us.get("^SOX") or {}).get("chg")
        if sox is not None and abs(sox) >= 2:
            lines.append(f"⚡ 費半{'大漲' if sox > 0 else '大跌'} {abs(sox):.1f}%，台股電子股開盤可能跟著{'走高' if sox > 0 else '走低'}，別在開盤第一盤追{'高' if sox > 0 else '殺'}")
    else:
        lines.append("（美股資料暫時抓不到）")

    ev = _upcoming_divs(d, today, 3)
    near = _near_alerts(d, lambda it: it.get("close"))
    if ev or near:
        lines.append("\n【今天注意】")
        lines += [f"• {x}" for x in ev + near]

    recap = _verdict_recap(d)
    if recap:
        lines.append(f"\n【昨日綜合訊號】（{(d.get('updated_at') or '')[:10]} 盤後）")
        lines += [f"• {x}" for x in recap]
    lines.append("\n12:30 會再推一次盤中買賣訊號。\n⚠️ " + DISCLAIMER)
    return "\n".join(lines)


# ---------------------------------------------------------------- 盤中
def midday_message(d: dict, quotes: dict, now: datetime) -> str:
    """用即時價對照前一次盤後的綜合訊號：偏多又回檔的、ETF 即時殖利率到門檻、到買賣點、持股偏空。"""
    stocks = [it for it in d.get("stocks") or [] if it.get("verdict")]
    live = {sid: q for sid, q in quotes.items() if q.is_live}

    def px(it):
        q = live.get(it["id"])
        return q.price if q else None

    def chg(it):
        q = live.get(it["id"])
        return q.change_pct if q else None

    lines = [f"⏱ {now:%m/%d %H:%M} 盤中買賣訊號"]
    idx = index_quote(quotes)
    if idx:
        lines.append(f"加權指數 {idx['close']:,.0f}（{_pct(idx['chg'])}）")
    buy, sell, etf_lines = [], [], []
    for it in stocks:
        v, p, c = it["verdict"], px(it), chg(it)
        if p is None:
            continue
        tag = f"{it['name']} {p:.2f}（{_pct(c)}）"
        ex_today = any(str(x.get("ex")) == now.date().isoformat() for x in it.get("dividends") or []) \
            or str(((it.get("signal") or {}).get("etf") or {}).get("next_ex")) == now.date().isoformat()
        e = (it.get("signal") or {}).get("etf")
        th = (it.get("alert") or {}).get("yield_above")
        if e is not None and th:
            dps = v.get("yield_dps") or e.get("ttm_dps")
            if dps:
                y = dps / p * 100
                if y >= th:
                    etf_lines.append(f"{tag}：殖利率 {y:.1f}% ≥ 你設的 {th:g}%，可考慮加碼")
                elif y >= th - 1:
                    etf_lines.append(f"{tag}：殖利率 {y:.1f}%，接近 {th:g}% 門檻（跌到 {dps / th * 100:.2f} 元達標）")
        if v["level"] >= 1:
            dip = c is not None and c <= -2
            buy.append(f"{tag}｜{v['label']} {v['score']:+d}，{v['action']}"
                       + ("；今天除息，跌幅含配息" if ex_today else "；今天回檔，可分批不追高" if dip else "")
                       + ("；今天大漲，等拉回" if c is not None and c >= 4 else ""))
        elif v["level"] <= -1 and v["held"]:
            sell.append(f"{tag}｜持股 {v['label']} {v['score']:+d}，{v['action']}")
        if v["held"] and c is not None and c <= -5 and not ex_today:
            sell.append(f"{tag}｜持股今天跌超過 5%，先別急著攤平，收盤後看法人")
    near = _near_alerts(d, px)
    if buy:
        lines.append("\n【可留意買進】（綜合偏多）")
        lines += [f"• {x}" for x in buy]
    if etf_lines:
        lines.append("\n【ETF 殖利率（即時價）】")
        lines += [f"• {x}" for x in etf_lines]
    if near:
        lines.append("\n【你設的買賣點】")
        lines += [f"• {x}" for x in near]
    if sell:
        lines.append("\n【留意賣出 / 減碼】")
        lines += [f"• {x}" for x in sell]
    if len(lines) <= 2:
        lines.append("\n今天盤中沒有特別的買賣訊號，照原計畫。")
    lines.append(f"\n評分是 {(d.get('updated_at') or '')[:10]} 盤後的綜合訊號，價格是即時價；盤後 17:30 會重新計算。"
                 "\n⚠️ " + DISCLAIMER)
    return "\n".join(lines)


# ---------------------------------------------------------------- 盤後（給 daily.py 用）
def postmarket_message(items: list[dict], prev: dict, pf_public: dict | None, idx: dict | None, today: date) -> str:
    """盤後分析段落：大盤、持股今日漲跌（依占比估算）、綜合評分明顯變動、明後天事件。"""
    lines = [f"🌇 {today:%m/%d} 盤後分析"]
    if idx and idx.get("close"):
        lines.append(f"加權指數 {idx['close']:,.0f}" + (f"（{idx['pts']:+,.0f} 點，{_pct(idx['chg'])}）" if idx.get("pts") is not None else ""))
    by_id = {it["id"]: it for it in items}
    rows = (pf_public or {}).get("rows") or []
    w = [(r, by_id.get(r["id"], {}).get("change_pct")) for r in rows if r.get("weight")]
    tot = sum(r["weight"] for r, c in w if c is not None)
    if tot:
        day = sum(r["weight"] * c for r, c in w if c is not None) / tot
        lines.append(f"你的持股今天約 {_pct(day)}：" + "、".join(f"{r['name']} {_pct(c)}" for r, c in w if c is not None))
    old = {s["id"]: s.get("verdict") for s in prev.get("stocks") or [] if s.get("verdict")}

    def pts(v):
        return {x["factor"]: x["pts"] for x in (v.get("buy") or []) + (v.get("sell") or [])}

    moves = []
    for it in items:
        v, o = it.get("verdict"), old.get(it["id"])
        if v and o and abs(v["score"] - o["score"]) >= 10:
            a, b = pts(o), pts(v)
            diff = sorted(((f, b.get(f, 0) - a.get(f, 0)) for f in set(a) | set(b)), key=lambda x: -abs(x[1]))
            why = "、".join(f"{f} {d:+d}" for f, d in diff[:2] if d)
            moves.append(f"{it['name']} {o['score']:+d} → {v['score']:+d}（{v['label']}）" + (f"，主要是{why}" if why else ""))
    if moves:
        lines.append("評分明顯變動：" + "；".join(moves))
    movers = sorted((it for it in items if it.get("change_pct") is not None), key=lambda it: it["change_pct"])
    if movers:
        dn = [f"{it['name']} {_pct(it['change_pct'])}" for it in movers[:3] if it["change_pct"] < 0]
        up = [f"{it['name']} {_pct(it['change_pct'])}" for it in movers[::-1][:3] if it["change_pct"] > 0]
        if up:
            lines.append("自選漲最多：" + "、".join(up))
        if dn:
            lines.append("自選跌最多：" + "、".join(dn))
    ev = _upcoming_divs({"stocks": items, "portfolio": pf_public}, today, 4, start=today + timedelta(days=1))
    if ev:
        lines.append("接下來：" + "；".join(ev))
    return "\n".join(lines)


def fetch_index() -> dict | None:
    try:
        return index_quote(data.live_quotes([(INDEX_ID, "twse")]))
    except Exception as e:  # noqa: BLE001
        log.info("加權指數抓取失敗：%s", e)
        return None


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["premarket", "midday"])
    p.add_argument("--force", action="store_true", help="不管休市 / 時段都跑")
    p.add_argument("--no-push", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    now = datetime.now(TPE)
    settings = config.load()
    if not settings.signal_notify:
        return 0
    today = now.date()
    if not args.force:
        if not in_window(args.kind, now):
            log.info("不在 %s 的發送時段（%s），結束", args.kind, now.strftime("%H:%M"))
            return 0
        if already_sent(args.kind, today):
            log.info("今天的 %s 已經發過，結束", args.kind)
            return 0
    d = _load_data()
    if args.kind == "premarket":
        if not args.force and is_holiday(today):
            log.info("今天休市，不發盤前分析")
            return 0
        msg = premarket_message(d, fetch_us(), today)
    else:
        pairs = [(it["id"], "tpex" if it.get("market") == "上櫃" else "twse") for it in d.get("stocks") or []]
        quotes = data.live_quotes(pairs + [(INDEX_ID, "twse")])
        ymd = now.strftime("%Y%m%d")
        if not args.force and quotes and all(q.date and q.date != ymd for q in quotes.values()):
            log.info("今天休市，結束")
            return 0
        msg = midday_message(d, quotes, now)
    if args.no_push:
        print(msg)
        return 0
    if notify.telegram(msg) and not args.force:   # 手動測試不算，免得擋掉當天正式那則
        mark_sent(args.kind, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
