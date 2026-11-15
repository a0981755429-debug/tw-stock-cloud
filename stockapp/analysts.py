"""分析師觀點專區：每天盤後彙整自選股的「分析師 / 法人怎麼看」→ docs/analysts.json。

資料來源（全部免費、只收標題與連結，內文請點連結到原媒體看）：
- 市場共識：Yahoo Finance（yfinance）的分析師人數、平均 / 最高 / 最低目標價、買進 / 持有 / 賣出家數、評等變動
- ADR 共識：台積電 TSM、聯電 UMC、日月光 ASX 的美國分析師共識（美元）
- 券商報告新聞：Google 新聞 RSS（中文）+ FinMind TaiwanStockNews，篩出跟「目標價、評等、外資報告」有關的標題，
  自動抓出券商名稱、目標價、偏多 / 偏空
- ETF 沒有分析師評等，改列配息、成分股調整等相關新聞，並並排「這次配息 × 4 年化」與「近一年實際」殖利率
- 收藏觀點：analyst_notes.yml（自己貼作者、摘要、連結）

本機測試：python -m stockapp.analysts
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import pandas as pd
import requests

from stockapp import config, data

log = logging.getLogger("analysts")
TPE = timezone(timedelta(hours=8))
GNEWS = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
ADR = {"2330": "TSM", "2303": "UMC", "3711": "ASX"}
LOOKBACK_DAYS = 60

# 越具體的放越前面（「摩根士丹利」要比「摩根」先比對到）
BROKERS = [
    ("摩根士丹利", "摩根士丹利"), ("大摩", "摩根士丹利"), ("摩根大通", "摩根大通"), ("小摩", "摩根大通"),
    ("高盛", "高盛"), ("美銀", "美銀"), ("花旗", "花旗"), ("瑞銀", "瑞銀"), ("麥格理", "麥格理"),
    ("野村", "野村"), ("里昂", "里昂"), ("滙豐", "滙豐"), ("匯豐", "滙豐"), ("德意志", "德意志"),
    ("巴克萊", "巴克萊"), ("傑富瑞", "傑富瑞"), ("伯恩斯坦", "伯恩斯坦"), ("大和", "大和"),
    ("凱基", "凱基"), ("元大", "元大"), ("富邦", "富邦"), ("國泰", "國泰"), ("群益", "群益"),
    ("統一投顧", "統一"), ("永豐", "永豐"), ("兆豐", "兆豐"), ("華南", "華南"), ("第一金", "第一金"),
    ("玉山", "玉山"), ("中信", "中信"), ("台新", "台新"), ("康和", "康和"), ("宏遠", "宏遠"),
    ("美系", "美系外資"), ("日系", "日系外資"), ("歐系", "歐系外資"), ("港系", "港系外資"),
    ("外資", "外資"), ("投顧", "投顧"), ("券商", "券商"), ("法人", "法人"),
]
BULL = ["調升", "上調", "調高", "上修", "買進", "優於大盤", "加碼", "看好", "增持", "樂觀", "上看", "喊進", "看多"]
BEAR = ["調降", "下調", "調低", "下修", "賣出", "劣於大盤", "減碼", "看淡", "減持", "保守", "看空", "砍"]
ANALYST_WORDS = ["目標價", "評等", "評級", "報告", "研報", "外資", "投顧", "券商", "法人", "喊", "優於大盤",
                 "劣於大盤", "買進", "中立", "加碼", "減碼"] + [b for b, _ in BROKERS[:-4]]
REC_ZH = {"strong_buy": "強力買進", "buy": "買進", "hold": "持有", "underperform": "劣於大盤",
          "sell": "賣出", "strongBuy": "強力買進", "strongSell": "強力賣出", "none": ""}
GRADE_ZH = {"Buy": "買進", "Strong Buy": "強力買進", "Outperform": "優於大盤", "Overweight": "加碼",
            "Hold": "持有", "Neutral": "中立", "Equal-Weight": "中立", "Market Perform": "中立",
            "Underperform": "劣於大盤", "Underweight": "減碼", "Sell": "賣出", "Strong Sell": "強力賣出"}
ACTION_ZH = {"up": "調升", "down": "調降", "main": "維持", "init": "首次評等", "reit": "重申"}


# ---------------------------------------------------------------- 標題解析
def broker_of(title: str) -> str:
    for kw, name in BROKERS:
        if kw in title:
            return name
    return ""


def target_of(title: str) -> float | None:
    """從標題抓目標價：「目標價300元」「目標價從250調升至300」「目標價上看 320」→ 取最後(調整後)的數字。"""
    i = title.find("目標價")
    if i < 0:
        return None
    seg = re.split(r"[，。；;！!？?、|｜]|,(?!\d{3})", title[i + 3:i + 30])[0]
    j = max(seg.rfind("至"), seg.rfind("到"))
    if j >= 0:
        seg = seg[j + 1:]
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\.\d+)?", seg)
    if not m:
        return None
    try:
        v = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return v if 5 <= v <= 20000 else None


def stance_of(title: str) -> str:
    b = sum(w in title for w in BULL)
    s = sum(w in title for w in BEAR)
    return "bull" if b > s else "bear" if s > b else "neutral"


def is_analyst_view(title: str) -> bool:
    return any(w in title for w in ANALYST_WORDS)


def parse_view(title: str, link: str, source: str, day: str) -> dict:
    t = title
    if source and t.endswith(" - " + source):      # Google 新聞標題尾巴會帶「 - 媒體名」
        t = t[: -len(source) - 3]
    return {"date": day, "title": t.strip(), "link": link, "source": source,
            "broker": broker_of(t), "target": target_of(t), "stance": stance_of(t)}


# ---------------------------------------------------------------- 新聞來源
def google_news(query: str) -> list[dict]:
    url = GNEWS.format(q=quote(query))
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (tw-stock-cloud personal dashboard)"}, timeout=20)
    r.raise_for_status()
    return parse_rss(r.text)


def parse_rss(xml_text: str) -> list[dict]:
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src = (it.findtext("source") or "").strip()
        try:
            day = parsedate_to_datetime(it.findtext("pubDate") or "").astimezone(TPE).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            day = ""
        if title and link:
            out.append({"title": title, "link": link, "source": src, "date": day})
    return out


def finmind_news(stock_id: str, start: str) -> list[dict]:
    df = data.finmind("TaiwanStockNews", stock_id, start)
    if df.empty:
        return []
    return [{"title": str(r.get("title") or ""), "link": str(r.get("link") or ""),
             "source": str(r.get("source") or ""), "date": str(r.get("date") or "")[:10]}
            for r in df.to_dict("records") if r.get("title") and r.get("link")]


def collect_news(stock_id: str, name: str, etf: bool, today: date) -> list[dict]:
    """回傳已去重、近 LOOKBACK_DAYS 天、新到舊排序的新聞（原始欄位）。"""
    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    queries = ([f"{name} 配息", f"{name} {stock_id} 成分股"] if etf
               else [f"{name} 目標價", f"{name} 外資 評等", f"{name} {stock_id} 法人"])
    raw: list[dict] = []
    for q in queries:
        try:
            raw += google_news(q + " when:60d")
        except Exception as e:  # noqa: BLE001
            log.warning("Google 新聞「%s」失敗：%s", q, e)
    try:
        raw += finmind_news(stock_id, (today - timedelta(days=7)).isoformat())
    except Exception as e:  # noqa: BLE001
        log.warning("FinMind 新聞 %s 失敗：%s", stock_id, e)
    seen, out = set(), []
    for n in sorted(raw, key=lambda x: x["date"], reverse=True):
        key = re.sub(r"\s|-.*$", "", n["title"])[:28]
        if n["date"] and n["date"] < since or key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


# ---------------------------------------------------------------- Yahoo 共識
def _clean(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 2)


def consensus(symbols: list[str]) -> dict | None:
    """Yahoo Finance 分析師共識。symbols 依序嘗試（2317.TW → 2317.TWO）。"""
    try:
        import yfinance as yf
    except ImportError:
        return None
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            pt = t.analyst_price_targets or {}
            info = {}
            try:
                info = t.info or {}
            except Exception:  # noqa: BLE001
                pass
            n = info.get("numberOfAnalystOpinions")
            if not pt.get("mean") and not n:
                continue
            out = {"symbol": sym, "n": int(n) if n else None,
                   "mean": _clean(pt.get("mean")), "median": _clean(pt.get("median")),
                   "high": _clean(pt.get("high")), "low": _clean(pt.get("low")),
                   "current": _clean(pt.get("current") or info.get("currentPrice")),
                   "currency": info.get("currency") or ("TWD" if sym.endswith((".TW", ".TWO")) else "USD"),
                   "key": REC_ZH.get(info.get("recommendationKey") or "none", info.get("recommendationKey") or ""),
                   "rec_mean": _clean(info.get("recommendationMean")), "rec": None, "changes": []}
            try:
                rec = t.recommendations
                if rec is not None and len(rec):
                    row = rec.iloc[0]
                    out["rec"] = {k: int(row.get(k, 0) or 0)
                                  for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
            except Exception:  # noqa: BLE001
                pass
            try:
                ud = t.upgrades_downgrades
                if ud is not None and len(ud):
                    cutoff = pd.Timestamp.now() - pd.Timedelta(days=365)
                    ud = ud.copy()
                    ud.index = pd.to_datetime(ud.index).tz_localize(None)
                    for d, r in ud[ud.index >= cutoff].sort_index(ascending=False).head(10).iterrows():
                        out["changes"].append({
                            "date": d.strftime("%Y-%m-%d"), "firm": str(r.get("Firm") or ""),
                            "to": GRADE_ZH.get(r.get("ToGrade"), r.get("ToGrade") or ""),
                            "from": GRADE_ZH.get(r.get("FromGrade"), r.get("FromGrade") or ""),
                            "action": ACTION_ZH.get(r.get("Action"), r.get("Action") or "")})
            except Exception:  # noqa: BLE001
                pass
            return out
        except Exception as e:  # noqa: BLE001
            log.info("yfinance 共識 %s 失敗：%s", sym, e)
    return None


# ---------------------------------------------------------------- 組合
def summarize(views: list[dict], close: float | None) -> dict:
    tg = [v for v in views if v["target"]]
    # 同一家券商只取最新一次目標價
    latest: dict[str, dict] = {}
    for v in tg:
        k = v["broker"] or v["title"]
        latest.setdefault(k, v)
    prices = [v["target"] for v in latest.values()]
    med = round(statistics.median(prices), 2) if prices else None
    return {"bull": sum(v["stance"] == "bull" for v in views),
            "bear": sum(v["stance"] == "bear" for v in views),
            "neutral": sum(v["stance"] == "neutral" for v in views),
            "targets": len(prices), "median_target": med,
            "upside_pct": round((med / close - 1) * 100, 1) if med and close else None}


def etf_yields(etf: dict | None, close: float | None) -> dict | None:
    """「這次配息 × 一年配幾次」的年化 vs「近一年實際配息合計」的殖利率，並排比較。

    年化的前提是接下來每次都配一樣多；配息會動態調整，編預算用近一年實際值比較保守。
    """
    if not etf or not close:
        return None
    dps = etf.get("next_dps") or etf.get("last_dps")
    freq = etf.get("count") or 4
    ttm = etf.get("ttm_dps")
    if not dps and not ttm:
        return None
    single = dps / close * 100 if dps else None
    out = {"dps": dps, "is_next": bool(etf.get("next_dps")), "freq": freq,
           "single_pct": round(single, 2) if single else None,
           "annualized_pct": round(single * freq, 2) if single else None,
           "ttm_dps": ttm, "ttm_pct": round(ttm / close * 100, 2) if ttm else None,
           "next_ex": etf.get("next_ex")}
    if out["annualized_pct"] and out["ttm_pct"]:
        out["gap_pct"] = round(out["annualized_pct"] - out["ttm_pct"], 2)
    return out


def load_notes(path=None) -> list[dict]:
    """analyst_notes.yml：自己收藏的分析師 / 部落客觀點（摘要 + 連結）。"""
    import yaml
    path = path or config.ROOT / "analyst_notes.yml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    out = []
    for n in raw.get("notes") or []:
        if not isinstance(n, dict) or not (n.get("title") or n.get("summary")):
            continue
        link = str(n.get("link") or "")
        out.append({"stock": str(n.get("stock") or "").strip(), "author": str(n.get("author") or ""),
                    "date": str(n.get("date") or ""), "title": str(n.get("title") or ""),
                    "summary": str(n.get("summary") or "").strip(),
                    "link": link if link.startswith(("https://", "http://")) else "",
                    "stance": n.get("stance") if n.get("stance") in ("bull", "bear", "neutral") else ""})
    return sorted(out, key=lambda x: x["date"], reverse=True)


def build(stock_id: str, name: str, close: float | None, market: str, today: date,
          etf_info: dict | None = None) -> dict:
    etf = data.is_etf(stock_id)
    news = collect_news(stock_id, name, etf, today)
    if etf:
        views = [parse_view(n["title"], n["link"], n["source"], n["date"]) for n in news[:12]]
        return {"id": stock_id, "name": name, "etf": True, "close": close, "views": views,
                "consensus": None, "adr": None, "summary": None, "yields": etf_yields(etf_info, close)}
    views = [parse_view(n["title"], n["link"], n["source"], n["date"])
             for n in news if is_analyst_view(n["title"])][:20]
    syms = [stock_id + s for s in ([".TWO", ".TW"] if market == "tpex" else [".TW", ".TWO"])]
    cons = consensus(syms)
    if cons and close and cons.get("mean"):
        cons["upside_pct"] = round((cons["mean"] / close - 1) * 100, 1)
    adr = consensus([ADR[stock_id]]) if stock_id in ADR else None
    if adr and adr.get("mean") and adr.get("current"):
        adr["upside_pct"] = round((adr["mean"] / adr["current"] - 1) * 100, 1)
    return {"id": stock_id, "name": name, "etf": False, "close": close, "views": views,
            "consensus": cons, "adr": adr, "summary": summarize(views, close)}


def _from_data_json() -> dict[str, dict]:
    try:
        j = json.loads((config.DOCS / "data.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {s["id"]: {"name": s.get("name"), "close": s.get("close"),
                      "etf": (s.get("signal") or {}).get("etf"),
                      "market": "tpex" if s.get("market") == "上櫃" else "twse"} for s in j.get("stocks") or []}


def main(argv=None) -> int:
    argparse.ArgumentParser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = config.load()
    ids = [s.id for s in settings.stocks] + [x for x in settings.analyst_extra
                                              if x not in {s.id for s in settings.stocks}]
    known = _from_data_json()
    info = data.stock_info() if any(i not in known for i in ids) else {}
    now = datetime.now(TPE)
    out = []
    for sid in ids:
        k = known.get(sid) or {}
        name = k.get("name") or info.get(sid, {}).get("name") or sid
        market = k.get("market") or info.get(sid, {}).get("market", "twse")
        log.info("分析師觀點 %s %s", sid, name)
        try:
            out.append(build(sid, name, k.get("close"), market, now.date(), k.get("etf")))
        except Exception as e:  # noqa: BLE001
            log.exception("%s 失敗：%s", sid, e)
    config.DOCS.mkdir(exist_ok=True)
    (config.DOCS / "analysts.json").write_text(
        json.dumps({"updated_at": now.strftime("%Y-%m-%d %H:%M"), "stocks": out, "notes": load_notes()},
                   ensure_ascii=False),
        encoding="utf-8")
    log.info("已寫入 docs/analysts.json（%d 檔）", len(out))
    return 0 if out or not ids else 1


if __name__ == "__main__":
    raise SystemExit(main())
