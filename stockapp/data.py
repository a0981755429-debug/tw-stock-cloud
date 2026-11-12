"""資料抓取：全部免費來源。

- 股票名稱 / 上市櫃別：FinMind TaiwanStockInfo
- 日 K 歷史：yfinance（2330.TW / 6488.TWO），失敗改用 FinMind TaiwanStockPrice
- 盤中即時價：證交所 mis.twse.com.tw（官網即時看盤頁的後端，非正式 API）
- 財報：FinMind 月營收、EPS、本益比（免費 dataset）

FinMind token 選用：沒有也能跑（每小時次數較少），在 GitHub Secrets 設 FINMIND_TOKEN 可提高上限。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests

log = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://mis.twse.com.tw/stock/index.jsp",
}


# ---------------------------------------------------------------- FinMind
def finmind(dataset: str, data_id: str | None = None, start_date: str | None = None) -> pd.DataFrame:
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    headers = {}
    token = os.environ.get("FINMIND_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        try:
            r = requests.get(FINMIND_URL, params=params, headers=headers, timeout=30)
            j = r.json()
            if r.status_code == 200 and j.get("status", 200) == 200:
                return pd.DataFrame(j.get("data") or [])
            log.warning("FinMind %s %s 回 %s: %s", dataset, data_id, r.status_code, str(j.get("msg"))[:120])
        except Exception as e:  # noqa: BLE001
            log.warning("FinMind %s %s 失敗: %s", dataset, data_id, e)
        time.sleep(2 * (attempt + 1))
    return pd.DataFrame()


def stock_info() -> dict[str, dict]:
    """{stock_id: {"name", "market"('twse'/'tpex'), "industry"}}"""
    df = finmind("TaiwanStockInfo")
    out: dict[str, dict] = {}
    if df.empty:
        return out
    for row in df.itertuples(index=False):
        sid = str(row.stock_id)
        if sid in out:
            continue
        out[sid] = {
            "name": row.stock_name,
            "market": row.type if row.type in ("twse", "tpex") else "twse",
            "industry": getattr(row, "industry_category", "") or "",
        }
    return out


# ---------------------------------------------------------------- 日 K
def price_history(stock_id: str, market: str, days: int = 400) -> pd.DataFrame:
    """回傳 index=date 的 DataFrame（open/high/low/close/volume，volume 單位：股）。"""
    df = _history_yfinance(stock_id, market)
    if df.empty:
        df = _history_finmind(stock_id, days)
    return df


def _history_yfinance(stock_id: str, market: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame()
    suffixes = [".TWO", ".TW"] if market == "tpex" else [".TW", ".TWO"]
    for suf in suffixes:
        try:
            h = yf.Ticker(stock_id + suf).history(period="2y", interval="1d", auto_adjust=False)
        except Exception as e:  # noqa: BLE001
            log.info("yfinance %s%s 失敗: %s", stock_id, suf, e)
            continue
        if h is not None and len(h) > 0:
            h = h.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
            return h.dropna(subset=["close"])
    return pd.DataFrame()


def _history_finmind(stock_id: str, days: int) -> pd.DataFrame:
    start = (date.today() - timedelta(days=int(days * 1.5))).isoformat()
    df = finmind("TaiwanStockPrice", stock_id, start)
    if df.empty:
        return df
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    df.index = pd.to_datetime(df["date"])
    return df[["open", "high", "low", "close", "volume"]].astype(float)


# ---------------------------------------------------------------- 盤中即時
@dataclass
class Quote:
    stock_id: str
    name: str
    price: float
    prev_close: float | None
    open: float | None
    high: float | None
    low: float | None
    volume_lots: float | None
    time: str
    date: str          # YYYYMMDD，交易所的資料日期（用來判斷今天是否開盤）
    is_live: bool      # False = 只拿到昨收

    @property
    def change_pct(self) -> float | None:
        if not self.prev_close:
            return None
        return (self.price / self.prev_close - 1) * 100


def _f(v) -> float | None:
    try:
        x = float(str(v).split("_")[0])
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def parse_mis(msg: dict) -> Quote | None:
    sid = msg.get("c")
    if not sid:
        return None
    y = _f(msg.get("y"))
    price = _f(msg.get("z")) or _f(msg.get("pz"))
    live = price is not None
    if price is None:
        a, b = _f(msg.get("a")), _f(msg.get("b"))
        if a and b:
            price, live = (a + b) / 2, True
        elif a or b:
            price, live = a or b, True
    if price is None:
        if y is None:
            return None
        price, live = y, False
    return Quote(
        stock_id=sid, name=msg.get("n", ""), price=price, prev_close=y,
        open=_f(msg.get("o")), high=_f(msg.get("h")), low=_f(msg.get("l")),
        volume_lots=_f(msg.get("v")), time=msg.get("t", ""), date=msg.get("d", ""),
        is_live=live,
    )


def live_quotes(pairs: list[tuple[str, str]]) -> dict[str, Quote]:
    """pairs = [(stock_id, market)]，一次 HTTP 抓全部。"""
    if not pairs:
        return {}
    ex = "|".join(f"{'otc' if m == 'tpex' else 'tse'}_{sid}.tw" for sid, m in pairs)
    try:
        r = requests.get(MIS_URL, params={"ex_ch": ex, "json": "1", "delay": "0"},
                         headers=_UA, timeout=15)
        msgs = r.json().get("msgArray") or []
    except Exception as e:  # noqa: BLE001
        log.warning("mis 即時報價失敗: %s", e)
        return {}
    out = {}
    for m in msgs:
        q = parse_mis(m)
        if q:
            out[q.stock_id] = q
    return out


# ---------------------------------------------------------------- 財報
def fundamentals(stock_id: str) -> dict:
    """月營收（含年增率）、EPS（近四季）、本益比 / 殖利率。抓不到的欄位就是 None。"""
    out: dict = {"revenue": [], "eps": [], "per": None, "pbr": None, "dividend_yield": None}
    start = (date.today() - timedelta(days=800)).isoformat()

    rev = finmind("TaiwanStockMonthRevenue", stock_id, start)
    if not rev.empty:
        rev = rev.sort_values(["revenue_year", "revenue_month"])
        key = {(int(r.revenue_year), int(r.revenue_month)): float(r.revenue) for r in rev.itertuples()}
        rows = []
        for (yy, mm), v in key.items():
            prev = key.get((yy - 1, mm))
            rows.append({
                "month": f"{yy}-{mm:02d}",
                "revenue": v,
                "yoy": round((v / prev - 1) * 100, 1) if prev else None,
            })
        out["revenue"] = rows[-13:]

    fs = finmind("TaiwanStockFinancialStatements", stock_id, start)
    if not fs.empty:
        eps = fs[fs["type"] == "EPS"].sort_values("date")
        out["eps"] = [{"quarter": _quarter(d), "eps": float(v)} for d, v in zip(eps["date"], eps["value"])][-8:]

    per = finmind("TaiwanStockPER", stock_id, (date.today() - timedelta(days=14)).isoformat())
    if not per.empty:
        last = per.sort_values("date").iloc[-1]
        out["per"] = _f(last.get("PER"))
        out["pbr"] = _f(last.get("PBR"))
        out["dividend_yield"] = float(last.get("dividend_yield") or 0) or None
    return out


def _quarter(d: str) -> str:
    y, m = int(d[:4]), int(d[5:7])
    return f"{y}Q{(m - 1) // 3 + 1}"
