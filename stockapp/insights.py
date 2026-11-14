"""FinMind 進階資料的整理（純計算，方便測試）：配息行事曆、三大法人、融資融券、外資持股、現金流、負債。

輸入都是 FinMind 回傳的 DataFrame（欄位名稱照 FinMind 官方文件），抓不到資料就回空 / None，不會丟例外。
單位：法人買賣超 FinMind 給「股」，這裡換成「張」（÷1000）；融資融券 FinMind 本來就是「張」。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

STREAK_MILESTONES = (3, 5, 10)   # 法人連買 / 連賣剛好達到這些天數時才推播，避免天天洗版


def _num(v) -> float:
    try:
        x = float(v)
        return 0.0 if pd.isna(x) else x
    except (TypeError, ValueError):
        return 0.0


def _date(v) -> str:
    s = str(v or "").strip()[:10]
    return s if len(s) == 10 and s[4] == "-" and s[:4].isdigit() else ""


def _yi(v: float | None) -> float | None:
    """元 → 億元"""
    return None if v is None else round(v / 1e8, 2)


# ---------------------------------------------------------------- 配息
def dividend_events(policy: pd.DataFrame, result: pd.DataFrame | None = None) -> list[dict]:
    """合併「股利政策表」（含已公告、還沒除息的）與「除權息結果表」（已除息）成一份現金配息清單。

    回傳依除息日排序：[{"ex": "2026-11-17", "dps": 1.25, "pay": "2026-12-12" 或 None}]
    """
    ev: dict[str, dict] = {}
    if policy is not None and not policy.empty and "CashExDividendTradingDate" in policy:
        for r in policy.to_dict("records"):
            ex = _date(r.get("CashExDividendTradingDate"))
            dps = _num(r.get("CashEarningsDistribution")) + _num(r.get("CashStatutorySurplus"))
            if ex and dps > 0:
                ev[ex] = {"ex": ex, "dps": round(dps, 4), "pay": _date(r.get("CashDividendPaymentDate")) or None}
    if result is not None and not result.empty and "stock_or_cache_dividend" in result:
        for r in result.to_dict("records"):
            ex = _date(r.get("date"))
            # 只取純「息」：「權息」的金額混了股票股利，無法拆出現金部分
            if ex and ex not in ev and str(r.get("stock_or_cache_dividend", "")).strip() == "息":
                dps = _num(r.get("stock_and_cache_dividend"))
                if dps > 0:
                    ev[ex] = {"ex": ex, "dps": round(dps, 4), "pay": None}
    return [ev[k] for k in sorted(ev)]


def next_dividend(events: list[dict], today: date) -> dict | None:
    t = today.isoformat()
    return next((e for e in events if e["ex"] >= t), None)


def ttm_dps(events: list[dict], today: date) -> float | None:
    start, t = (today - timedelta(days=365)).isoformat(), today.isoformat()
    xs = [e["dps"] for e in events if start < e["ex"] <= t]
    return round(sum(xs), 4) if xs else None


def received(events: list[dict], since: str, today: date) -> tuple[float, int]:
    """since（第一次買進日）之後、今天以前除息的每股配息合計與次數。

    需要在除息日「前」就持有才領得到，所以條件是 since < 除息日。
    """
    t = today.isoformat()
    xs = [e["dps"] for e in events if since < e["ex"] <= t]
    return round(sum(xs), 4), len(xs)


# ---------------------------------------------------------------- 三大法人
_GROUPS = {"Foreign_Investor": "foreign", "Foreign_Dealer_Self": "foreign",
           "Investment_Trust": "trust",
           "Dealer": "dealer", "Dealer_self": "dealer", "Dealer_Hedging": "dealer"}
_GROUP_NAME = {"foreign": "外資", "trust": "投信", "dealer": "自營商"}


def _streak(s: pd.Series) -> int:
    """從最後一天往回數連續同方向的天數：+N = 連買 N 天，-N = 連賣 N 天。"""
    vals = [v for v in s.tolist()]
    if not vals or vals[-1] == 0:
        return 0
    sign = 1 if vals[-1] > 0 else -1
    n = 0
    for v in reversed(vals):
        if v * sign > 0:
            n += 1
        else:
            break
    return n * sign


def institutional(df: pd.DataFrame) -> dict | None:
    """回傳 {"date", "days": [[日期, 外資, 投信, 自營]...近 10 天, 張], "foreign": {...}, "trust": {...}, "dealer": {...}, "events": [...]}"""
    if df is None or df.empty or not {"date", "name", "buy", "sell"} <= set(df.columns):
        return None
    d = df.copy()
    d["g"] = d["name"].map(_GROUPS)
    d = d.dropna(subset=["g"])
    if d.empty:
        return None
    d["net"] = (pd.to_numeric(d["buy"], errors="coerce").fillna(0)
                - pd.to_numeric(d["sell"], errors="coerce").fillna(0)) / 1000
    w = d.pivot_table(index="date", columns="g", values="net", aggfunc="sum").sort_index().fillna(0)
    for g in _GROUP_NAME:
        if g not in w:
            w[g] = 0.0
    out = {"date": str(w.index[-1])[:10], "events": [],
           "days": [[str(i)[:10], round(r.foreign), round(r.trust), round(r.dealer)] for i, r in w.tail(10).iterrows()]}
    for g, label in _GROUP_NAME.items():
        s = w[g]
        st = _streak(s)
        out[g] = {"today": round(s.iloc[-1]), "sum5": round(s.tail(5).sum()), "sum20": round(s.tail(20).sum()),
                  "streak": st}
        if g != "dealer" and abs(st) in STREAK_MILESTONES:
            tot = round(s.tail(abs(st)).sum())
            out["events"].append(f"{label}連續{'買超' if st > 0 else '賣超'} {abs(st)} 天（累計 {tot:+,} 張）")
    return out


def institutional_reasons(x: dict | None) -> list[str]:
    if not x:
        return []
    out = []
    for g in ("foreign", "trust"):
        v = x[g]
        st = v["streak"]
        trend = f"，連{'買' if st > 0 else '賣'} {abs(st)} 天" if abs(st) >= 2 else ""
        out.append(f"{_GROUP_NAME[g]}今日 {v['today']:+,} 張、近 5 日 {v['sum5']:+,} 張、近 20 日 {v['sum20']:+,} 張{trend}")
    return out


# ---------------------------------------------------------------- 融資融券
def margin(df: pd.DataFrame) -> dict | None:
    """融資餘額、增減、使用率、券資比。過熱判斷：融資 20 日增加超過 15%。"""
    need = {"date", "MarginPurchaseTodayBalance"}
    if df is None or df.empty or not need <= set(df.columns):
        return None
    d = df.sort_values("date").reset_index(drop=True)
    bal = pd.to_numeric(d["MarginPurchaseTodayBalance"], errors="coerce").fillna(0)
    short = pd.to_numeric(d.get("ShortSaleTodayBalance", pd.Series([0] * len(d))), errors="coerce").fillna(0)
    limit = pd.to_numeric(d.get("MarginPurchaseLimit", pd.Series([0] * len(d))), errors="coerce").fillna(0)
    b = float(bal.iloc[-1])

    def chg(n):
        if len(bal) <= n or not bal.iloc[-1 - n]:
            return None
        return round((b / float(bal.iloc[-1 - n]) - 1) * 100, 1)

    c20 = chg(20)
    prev20 = (round((float(bal.iloc[-2]) / float(bal.iloc[-22]) - 1) * 100, 1)
              if len(bal) > 21 and bal.iloc[-22] else None)
    out = {"date": str(d["date"].iloc[-1])[:10], "balance": round(b), "change_1d": round(b - float(bal.iloc[-2])) if len(bal) > 1 else None,
           "change_5d_pct": chg(5), "change_20d_pct": c20,
           "usage_pct": round(b / float(limit.iloc[-1]) * 100, 2) if limit.iloc[-1] else None,
           "short_balance": round(float(short.iloc[-1])),
           "short_ratio_pct": round(float(short.iloc[-1]) / b * 100, 2) if b else None,
           "hot": bool(c20 is not None and c20 > 15), "events": []}
    if out["hot"] and not (prev20 is not None and prev20 > 15):
        out["events"].append(f"融資餘額 20 日增加 {c20:.0f}%，散戶追價、籌碼變亂，留意過熱")
    return out


def margin_reasons(m: dict | None) -> list[str]:
    if not m:
        return []
    s = f"融資餘額 {m['balance']:,} 張"
    if m["change_20d_pct"] is not None:
        s += f"（20 日 {m['change_20d_pct']:+.1f}%）"
    if m["usage_pct"] is not None:
        s += f"，使用率 {m['usage_pct']:.1f}%"
    if m["short_ratio_pct"] is not None:
        s += f"，券資比 {m['short_ratio_pct']:.1f}%"
    if m["hot"]:
        s += "，偏過熱"
    elif m["change_20d_pct"] is not None and m["change_20d_pct"] < -10:
        s += "，融資退場、籌碼變乾淨"
    return [s]


# ---------------------------------------------------------------- 外資持股
def foreign_holding(df: pd.DataFrame) -> dict | None:
    if df is None or df.empty or "ForeignInvestmentSharesRatio" not in df:
        return None
    d = df.sort_values("date")
    r = pd.to_numeric(d["ForeignInvestmentSharesRatio"], errors="coerce").dropna()
    if r.empty:
        return None
    last = float(r.iloc[-1])

    def chg(n):
        return round(last - float(r.iloc[-1 - n]), 2) if len(r) > n else None

    return {"date": str(d["date"].iloc[-1])[:10], "ratio": round(last, 2),
            "change_20d": chg(20), "change_60d": chg(60),
            "series": [round(float(v), 2) for v in r.tail(60)]}


def foreign_reasons(f: dict | None) -> list[str]:
    if not f:
        return []
    s = f"外資持股 {f['ratio']:.2f}%"
    parts = []
    if f["change_20d"] is not None:
        parts.append(f"近一個月 {f['change_20d']:+.2f} 個百分點")
    if f["change_60d"] is not None:
        parts.append(f"近三個月 {f['change_60d']:+.2f}")
    return [s + ("（" + "、".join(parts) + "）" if parts else "")]


# ---------------------------------------------------------------- 現金流 / 負債
def _single_quarters(df: pd.DataFrame, type_name: str) -> dict[str, float]:
    """現金流量表是「年初累計」，換成單季：Q1 照用，其他季減上一季。"""
    x = df[df["type"] == type_name]
    ytd = {str(d)[:10]: float(v) for d, v in zip(x["date"], pd.to_numeric(x["value"], errors="coerce")) if pd.notna(v)}
    out = {}
    for d, v in ytd.items():
        y, m = int(d[:4]), int(d[5:7])
        q = (m - 1) // 3 + 1
        key = f"{y}Q{q}"
        if q == 1:
            out[key] = v
        else:
            prev = next((pv for pd_, pv in ytd.items() if int(pd_[:4]) == y and (int(pd_[5:7]) - 1) // 3 + 1 == q - 1), None)
            if prev is not None:
                out[key] = v - prev
    return out


def cash_flow(df: pd.DataFrame) -> dict | None:
    """營業現金流、資本支出、自由現金流（近 4 季合計 + 每季），單位億元。"""
    if df is None or df.empty or not {"date", "type", "value"} <= set(df.columns):
        return None
    ocf = _single_quarters(df, "CashFlowsFromOperatingActivities")
    capex = _single_quarters(df, "PropertyAndPlantAndEquipment")  # 取得不動產、廠房及設備（負數）
    if not ocf:
        return None
    qs = sorted(ocf)[-8:]
    rows = []
    for q in qs:
        c = capex.get(q, 0.0)
        rows.append({"quarter": q, "ocf": _yi(ocf[q]), "capex": _yi(c), "fcf": _yi(ocf[q] + c)})
    last4 = rows[-4:]
    ttm = None
    if len(last4) == 4:
        ttm = {"ocf": round(sum(r["ocf"] for r in last4), 2), "capex": round(sum(r["capex"] for r in last4), 2),
               "fcf": round(sum(r["fcf"] for r in last4), 2)}
    return {"quarters": rows, "ttm": ttm}


def balance(df: pd.DataFrame) -> dict | None:
    """負債比、流動比（最新一季）。用中文科目名稱比對，FinMind 英文 type 名稱各公司不一。"""
    if df is None or df.empty or not {"date", "type", "value", "origin_name"} <= set(df.columns):
        return None
    d = df[~df["type"].astype(str).str.endswith("_per")]
    last = d["date"].max()
    d = d[d["date"] == last]

    def pick(*names):
        for n in names:
            x = d[d["origin_name"].astype(str).str.strip() == n]
            if not x.empty:
                v = pd.to_numeric(x["value"], errors="coerce").dropna()
                if not v.empty:
                    return float(v.iloc[0])
        return None

    assets = pick("資產總計", "資產總額")
    liab = pick("負債總計", "負債總額")
    ca, cl = pick("流動資產合計"), pick("流動負債合計")
    if assets is None and liab is None:
        return None
    return {"date": str(last)[:10],
            "debt_ratio": round(liab / assets * 100, 1) if assets and liab is not None else None,
            "current_ratio": round(ca / cl * 100, 0) if ca and cl else None}


def health_reasons(cf: dict | None, bs: dict | None) -> list[str]:
    out = []
    if cf and cf.get("ttm"):
        t = cf["ttm"]
        s = f"近四季自由現金流 {t['fcf']:+,.1f} 億（營業現金流 {t['ocf']:,.1f} 億 − 資本支出 {abs(t['capex']):,.1f} 億）"
        s += "，本業真的有收到現金" if t["fcf"] > 0 else "，錢花在擴產比賺進來的多，留意配息能否維持"
        out.append(s)
    if bs and bs.get("debt_ratio") is not None:
        s = f"負債比 {bs['debt_ratio']:.0f}%"
        if bs.get("current_ratio"):
            s += f"、流動比 {bs['current_ratio']:.0f}%"
            if bs["current_ratio"] < 100:
                s += "（短期償債能力偏弱）"
        out.append(s)
    return out
