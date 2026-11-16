"""綜合買賣訊號：把平台已經收集的各種資料，用「公開、固定的加減分規則」整理成一個結論。

每個因子都會列出「支持買進」或「支持賣出 / 暫緩」的理由與分數，全部加總後換成結論：
  總分 ≥ +40：強烈偏多     +15 ~ +39：偏多     -14 ~ +14：中性
  -15 ~ -39：偏空           ≤ -40：強烈偏空
結論文字會依「有沒有持有」調整（有持股說加碼 / 減碼，沒持股說買進 / 避開）。

個股因子（滿分）：技術面 ±30、法人 ±15、融資 −10/+5、外資持股 ±5、分析師目標價 +10/−10、新聞評等 ±5、
  月營收 ±10、EPS 成長 ±5、本益比 ±5、殖利率 +5、財務體質 −10、你設的買賣點 ±10、重大訊息 ±5、持股集中度 −10
ETF 因子（走總報酬 / 存股思維）：殖利率 vs 你設的加碼門檻 +30 ~ −10、折溢價 −10/+5、技術面 ±10（大盤型 ±30）、
  持股集中度 −10；年化配息率與除息日只提醒、不加分。

這是規則式參考工具，不是投資建議；資料可能延遲或缺漏，下單前請自己再確認。
純計算（不連網），方便測試。
"""
from __future__ import annotations

from datetime import date, timedelta

DISCLAIMER = "綜合訊號是依固定規則加總的參考工具，不是投資建議，也不保證獲利；資料可能延遲或缺漏，請自行判斷。"
LEVELS = [(40, 2, "強烈偏多"), (15, 1, "偏多"), (-14, 0, "中性"), (-39, -1, "偏空"), (-999, -2, "強烈偏空")]
ACTIONS = {  # (有持股, 沒持股)
    2: ("可考慮加碼", "可考慮分批買進"),
    1: ("續抱，可小量加碼", "可列入買進觀察"),
    0: ("續抱觀察", "觀望"),
    -1: ("暫不加碼，留意風險", "暫不進場"),
    -2: ("可考慮減碼", "避開"),
}
BROAD_ETF = {"0050", "006208", "00692", "0051", "00850", "00881"}   # 大盤 / 市值型：沒有殖利率門檻，看技術面
GOOD_MOPS = ("庫藏股", "買回", "股利", "配息", "增資發行新股", "調升", "得標", "簽訂", "取得")
BAD_MOPS = ("處分", "減資", "停工", "違約", "裁罰", "罰鍰", "虧損", "跳票", "重整", "下修", "停止", "解任", "辭任",
            "火災", "訴訟", "退票", "掏空", "更正")


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


class _Board:
    def __init__(self):
        self.buy: list[dict] = []
        self.sell: list[dict] = []
        self.info: list[str] = []
        self.missing: list[str] = []

    def add(self, factor: str, pts: int, text: str):
        if pts > 0:
            self.buy.append({"factor": factor, "pts": pts, "text": text})
        elif pts < 0:
            self.sell.append({"factor": factor, "pts": pts, "text": text})
        else:
            self.info.append(f"{factor}：{text}")

    def result(self, held: bool) -> dict:
        score = max(-100, min(100, sum(x["pts"] for x in self.buy + self.sell)))
        level, label = next((lv, lb) for th, lv, lb in LEVELS if score >= th)
        self.buy.sort(key=lambda x: -x["pts"])
        self.sell.sort(key=lambda x: x["pts"])
        return {"score": score, "level": level, "label": label, "held": held,
                "action": ACTIONS[level][0 if held else 1],
                "buy": self.buy, "sell": self.sell, "info": self.info, "missing": self.missing}


def _tech(b: _Board, tech: dict | None, weight: float, factor="技術面"):
    if not tech or tech.get("label") == "資料不足":
        b.missing.append("技術面（歷史資料不足）")
        return
    pts = round((tech.get("score") or 0) * weight)
    lead = next((r for r in tech.get("reasons") or [] if "均線" in r or "排列" in r or "季線" in r), "")
    txt = f"技術分數 {tech.get('score', 0):+d}（{tech.get('label')}）" + (f"：{lead}" if lead else "")
    b.add(factor, pts, txt)
    for ev in tech.get("events") or []:   # 今天剛出現的交叉 / 爆量，已含在技術分數裡，只列出來
        if any(k in ev for k in ("交叉", "MACD", "帶量")):
            b.info.append(f"今日技術事件：{ev}")


def _concentration(b: _Board, hold: dict | None, etf: bool):
    w = (hold or {}).get("weight")
    if w is None:
        return
    lim = 60 if etf else 30
    if w > lim:
        b.add("持股集中度", -10, f"已占持股 {w:.0f}%（{'ETF' if etf else '個股'}超過 {lim}%），再加碼風險集中")


def _holding_info(b: _Board, hold: dict | None):
    if not hold:
        return
    parts = []
    if hold.get("pl_pct") is not None:
        parts.append(f"股價報酬 {hold['pl_pct']:+.1f}%")
    if hold.get("total_pl_pct") is not None:
        parts.append(f"含息總報酬 {hold['total_pl_pct']:+.1f}%")
    if hold.get("weight") is not None:
        parts.append(f"占持股 {hold['weight']:.0f}%")
    if parts:
        b.info.append("你的持股（永豐庫存）：" + "、".join(parts))


# ---------------------------------------------------------------- 個股
def stock(item: dict, analyst: dict | None = None, hold: dict | None = None, today: date | None = None) -> dict:
    """item = data.json 的一檔（含 signal / chips / fundamentals）；analyst = analysts.json 的同一檔；hold = 持股公開列。"""
    today = today or date.today()
    b = _Board()
    close = _f(item.get("close"))
    _tech(b, item.get("signal"), 0.3)

    # 法人（外資 + 投信，自營商短線進出不列入）
    inst = (item.get("chips") or {}).get("institutional")
    if inst:
        f5, t5 = inst["foreign"]["sum5"], inst["trust"]["sum5"]
        pts, txt = 0, f"近 5 日外資 {f5:+,} 張、投信 {t5:+,} 張"
        if f5 > 0 and t5 > 0:
            pts, txt = 10, txt + "，同步買超"
        elif f5 < 0 and t5 < 0:
            pts, txt = -10, txt + "，同步賣超"
        elif f5 + t5 > 0:
            pts = 5
        elif f5 + t5 < 0:
            pts = -5
        for g, nm in (("foreign", "外資"), ("trust", "投信")):
            st = inst[g]["streak"]
            if abs(st) >= 3:
                pts += 5 if st > 0 else -5
                txt += f"；{nm}連{'買' if st > 0 else '賣'} {abs(st)} 天"
        b.add("三大法人", max(-15, min(15, pts)), txt)
    else:
        b.missing.append("三大法人")

    m = (item.get("chips") or {}).get("margin")
    if m:
        c20 = m.get("change_20d_pct")
        if m.get("hot"):
            b.add("融資", -10, f"融資 20 日增加 {c20:.0f}%，散戶追價、籌碼偏亂")
        elif c20 is not None and c20 < -10:
            b.add("融資", 5, f"融資 20 日減少 {abs(c20):.0f}%，籌碼變乾淨")
        elif c20 is not None:
            b.add("融資", 0, f"融資 20 日 {c20:+.1f}%，正常")
    else:
        b.missing.append("融資融券")

    fh = (item.get("chips") or {}).get("foreign")
    if fh and fh.get("change_20d") is not None:
        c = fh["change_20d"]
        b.add("外資持股", 5 if c >= 0.5 else -5 if c <= -0.5 else 0,
              f"外資持股 {fh['ratio']:.1f}%，近一個月 {c:+.2f} 個百分點")

    # 分析師：Yahoo 共識目標價（用今天收盤重算空間）
    cons = (analyst or {}).get("consensus")
    if cons and cons.get("mean") and close:
        up = (cons["mean"] / close - 1) * 100
        n = cons.get("n") or 0
        pts = 10 if up >= 20 else 5 if up >= 10 else -10 if up < 0 else 0
        if n < 3:
            pts = round(pts / 2)
        txt = f"{n or '?'} 位分析師平均目標價 {cons['mean']:g}，距現價 {up:+.0f}%" + ("（人數少，分數減半）" if n < 3 else "")
        if cons.get("key"):
            txt += f"，共識評等「{cons['key']}」"
        b.add("分析師目標價", pts, txt)
    else:
        b.missing.append("分析師共識")
    sm = (analyst or {}).get("summary") or {}
    bull, bear = sm.get("bull") or 0, sm.get("bear") or 0
    if bull - bear >= 3:
        b.add("新聞評等", 5, f"近 60 天券商新聞偏多 {bull} 則、偏空 {bear} 則")
    elif bear - bull >= 3:
        b.add("新聞評等", -5, f"近 60 天券商新聞偏空 {bear} 則、偏多 {bull} 則")

    # 基本面
    f = item.get("fundamentals") or {}
    rev = [r["yoy"] for r in (f.get("revenue") or [])[-3:] if r.get("yoy") is not None]
    if len(rev) >= 2:
        avg = sum(rev) / len(rev)
        b.add("月營收", 10 if avg >= 10 else -10 if avg <= -10 else 0, f"近 {len(rev)} 個月營收年增率平均 {avg:+.1f}%")
    else:
        b.missing.append("月營收")
    eps = [e["eps"] for e in (f.get("eps") or [])]
    if len(eps) >= 8:
        now4, prev4 = sum(eps[-4:]), sum(eps[-8:-4])
        if prev4 > 0:
            g = (now4 / prev4 - 1) * 100
            b.add("EPS", 5 if g >= 10 else -5 if g <= -10 else 0, f"近四季 EPS {now4:.2f} 元，比前四季 {g:+.0f}%")
        elif now4 < 0:
            b.add("EPS", -5, f"近四季 EPS {now4:.2f} 元（虧損）")
    per = _f(f.get("per"))
    if per:
        b.add("本益比", -5 if per > 40 else 5 if per < 12 else 0, f"本益比 {per:.1f} 倍" +
              ("，偏貴" if per > 40 else "，偏便宜" if per < 12 else ""))
    dy = _f(f.get("dividend_yield"))
    if dy and dy >= 5:
        b.add("殖利率", 5, f"現金殖利率 {dy:.1f}%，領息有支撐（總報酬 = 股息 + 價差）")

    h = f.get("health") or {}
    hp, ht = 0, []
    ttm = (h.get("cash_flow") or {}).get("ttm")
    if ttm and ttm.get("fcf") is not None and ttm["fcf"] < 0:
        hp -= 5
        ht.append(f"近四季自由現金流 {ttm['fcf']:+,.1f} 億")
    bs = h.get("balance") or {}
    fin = "金融" in (item.get("industry") or "")
    if bs.get("debt_ratio") is not None and bs["debt_ratio"] > 70 and not fin:
        hp -= 5
        ht.append(f"負債比 {bs['debt_ratio']:.0f}%")
    if hp:
        b.add("財務體質", hp, "、".join(ht))

    # 你自己在 watchlist.yml 設的波段買賣點
    a = item.get("alert") or {}
    if close and a.get("below") is not None and close <= a["below"]:
        b.add("你設的買點", 10, f"收盤 {close:g} ≤ 你設的買點 {a['below']:g}")
    if close and a.get("above") is not None and close >= a["above"]:
        b.add("你設的賣點", -10, f"收盤 {close:g} ≥ 你設的賣點 {a['above']:g}")

    _mops(b, (analyst or {}).get("mops") or [], today)
    _concentration(b, hold, etf=False)
    _holding_info(b, hold)
    return b.result(bool(hold))


def _mops(b: _Board, items: list[dict], today: date):
    since = (today - timedelta(days=7)).isoformat()
    recent = [m for m in items if (m.get("date") or "") >= since]
    if not recent:
        return
    good = [m for m in recent if any(k in m.get("subject", "") for k in GOOD_MOPS)]
    bad = [m for m in recent if any(k in m.get("subject", "") for k in BAD_MOPS)]
    pts = (5 if good else 0) - (5 if bad else 0)
    subj = "；".join(f"{m['date'][5:]} {m['subject'][:40]}" for m in recent[:3])
    b.add("重大訊息", pts, f"近 7 天 {len(recent)} 則：{subj}" + ("（關鍵字判斷，請點進去看內容）" if pts else ""))


# ---------------------------------------------------------------- ETF
def etf(item: dict, tech: dict | None = None, analyst: dict | None = None, hold: dict | None = None,
        today: date | None = None) -> dict:
    """ETF 走總報酬思維：高股息看「殖利率是否到你設的加碼門檻」，大盤型看技術面；不使用停損。"""
    today = today or date.today()
    b = _Board()
    close = _f(item.get("close"))
    e = (item.get("signal") or {}).get("etf") or {}
    th = (item.get("alert") or {}).get("yield_above")
    yl = (analyst or {}).get("yields") or {}
    sid = item.get("id", "")

    # 殖利率：優先用「近 N 次實際配息（含已公告這次）」÷ 今天收盤，比較貼近真的領得到的錢
    y, basis = None, ""
    if yl.get("recent_dps") and close:
        y = yl["recent_dps"] / close * 100
        basis = f"近 {yl.get('freq', 4)} 次配息 {yl['recent_dps']:g} 元" + ("（含已公告這次）" if yl.get("recent_incl_next") else "")
    elif e.get("yield"):
        y, basis = e["yield"], f"近一年配息 {e.get('ttm_dps') or 0:g} 元"
    if th is not None:
        if y is None:
            b.missing.append("殖利率（配息資料）")
        elif y >= th:
            b.add("殖利率門檻", 30, f"{basis}，以現價計 {y:.1f}%，已達你設的加碼門檻 {th:g}%")
        elif y >= th - 1:
            b.add("殖利率門檻", 10, f"{basis}，以現價計 {y:.1f}%，接近你設的 {th:g}% 門檻")
        elif y < th - 3:
            b.add("殖利率門檻", -10, f"{basis}，以現價計 {y:.1f}%，離你設的 {th:g}% 門檻還遠（價格偏貴）")
        else:
            b.add("殖利率門檻", 0, f"{basis}，以現價計 {y:.1f}%，還沒到你設的 {th:g}% 門檻")
    elif y is not None:
        b.info.append(f"殖利率：{basis}，以現價計 {y:.1f}%")

    if yl.get("annualized_pct") and y and yl["annualized_pct"] - y > 1:
        b.info.append(f"年化配息率 {yl['annualized_pct']:.1f}%（單次 × {yl.get('freq', 4)}）高於實際 {y:.1f}%，"
                      "配息會調整，別只看年化數字；長期看含息總報酬")

    p = _f(e.get("premium_pct"))
    if p is not None:
        b.add("折溢價", -10 if p > 1 else 5 if p < -0.5 else 0,
              f"市價相對淨值 {p:+.2f}%" + ("，溢價偏高別追" if p > 1 else "，折價相對便宜" if p < -0.5 else "，接近淨值"))

    broad = sid in BROAD_ETF or th is None
    _tech(b, tech, 0.3 if broad else 0.1, "技術面（擇時參考）" if not broad else "技術面")

    nx = e.get("next_ex") or ""
    if len(nx) == 10 and nx[4] == "-":
        try:
            d = (date.fromisoformat(nx) - today).days
            if 0 <= d <= 14:
                b.info.append(f"{nx} 除息（{d} 天後）：除息前買才領得到，但股價會扣掉配息，總報酬不會因此變多")
        except ValueError:
            pass

    _concentration(b, hold, etf=True)
    _holding_info(b, hold)
    res = b.result(bool(hold))
    res["yield_dps"] = yl.get("recent_dps") or e.get("ttm_dps")   # 盤中用即時價重算殖利率
    return res


def one_line(it: dict) -> str:
    """Telegram 用：一檔一行 + 前兩個買 / 賣理由。"""
    v = it["verdict"]
    lines = [f"{it['id']} {it['name']}｜{v['label']} {v['score']:+d} → {v['action']}"]
    if v["buy"]:
        lines.append("  ✅ " + "；".join(x["text"] for x in v["buy"][:2]))
    if v["sell"]:
        lines.append("  ⚠️ " + "；".join(x["text"] for x in v["sell"][:2]))
    return "\n".join(lines)


def summary_message(items: list[dict]) -> str:
    xs = [it for it in items if it.get("verdict")]
    if not xs:
        return ""
    xs.sort(key=lambda it: (not it["verdict"]["held"], -it["verdict"]["score"]))
    held = [it for it in xs if it["verdict"]["held"]]
    watch = [it for it in xs if not it["verdict"]["held"]]
    out = ["🧭 綜合買賣訊號（技術＋籌碼＋基本面＋分析師＋殖利率）"]
    if held:
        out.append("【持股】")
        out += [one_line(it) for it in held]
    picks = [it for it in watch if it["verdict"]["level"] >= 1]
    avoid = [it for it in watch if it["verdict"]["level"] <= -1]
    if picks:
        out.append("【自選・偏多】")
        out += [one_line(it) for it in picks]
    if avoid:
        out.append("【自選・偏空】" + "、".join(f"{it['name']} {it['verdict']['score']:+d}" for it in avoid))
    rest = [it for it in watch if it["verdict"]["level"] == 0]
    if rest:
        out.append("【自選・中性】" + "、".join(f"{it['name']} {it['verdict']['score']:+d}" for it in rest))
    out.append("完整理由請看網頁每檔的「綜合訊號」\n⚠️ " + DISCLAIMER)
    return "\n".join(out)
