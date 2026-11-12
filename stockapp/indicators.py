"""技術指標（只用 pandas，不依賴 TA 套件）。"""
from __future__ import annotations

import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, float("nan"))
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    dif = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": dif - dea})


def kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9) -> pd.DataFrame:
    """台灣常用 KD（RSV 9 日，K、D 以 1/3 平滑，起始 50）。"""
    ll = low.rolling(n, min_periods=n).min()
    hh = high.rolling(n, min_periods=n).max()
    rng = (hh - ll).replace(0, float("nan"))
    rsv = ((close - ll) / rng * 100).fillna(50)
    k_vals, d_vals = [], []
    k = d = 50.0
    for v in rsv:
        k = k * 2 / 3 + v / 3
        d = d * 2 / 3 + k / 3
        k_vals.append(k)
        d_vals.append(d)
    return pd.DataFrame({"k": k_vals, "d": d_vals}, index=close.index)
