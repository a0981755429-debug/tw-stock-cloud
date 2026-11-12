"""買賣訊號：把多個技術指標整理成「偏多 / 偏空 / 中性」+ 白話理由。

這是規則式的參考訊號，不是投資建議，也不保證獲利。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from stockapp.indicators import kd, macd, rsi, sma


@dataclass
class SignalResult:
    score: int                      # -100 ~ +100，正數偏多
    label: str                      # 偏多 / 偏空 / 中性 / 資料不足
    reasons: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)   # 「今天剛發生」的事件（黃金交叉等），會推播
    indicators: dict = field(default_factory=dict)


def _crossed_up(a: pd.Series, b: pd.Series) -> bool:
    return len(a) >= 2 and a.iloc[-2] <= b.iloc[-2] and a.iloc[-1] > b.iloc[-1]


def _crossed_down(a: pd.Series, b: pd.Series) -> bool:
    return len(a) >= 2 and a.iloc[-2] >= b.iloc[-2] and a.iloc[-1] < b.iloc[-1]


def analyze(df: pd.DataFrame) -> SignalResult:
    """df 需有 open/high/low/close/volume 欄位，依日期排序。"""
    if df is None or len(df) < 60:
        return SignalResult(0, "資料不足", ["歷史資料少於 60 天"])

    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
    ma5, ma20, ma60 = sma(close, 5), sma(close, 20), sma(close, 60)
    r = rsi(close)
    m = macd(close)
    k = kd(high, low, close)
    vol20 = sma(vol, 20)

    last = float(close.iloc[-1])
    score = 0
    reasons: list[str] = []
    events: list[str] = []

    # 1. 趨勢：股價與均線位置（權重最大）
    if last > ma20.iloc[-1] > ma60.iloc[-1]:
        score += 30
        reasons.append("股價在月線、季線之上，且月線高於季線（多頭排列）")
    elif last < ma20.iloc[-1] < ma60.iloc[-1]:
        score -= 30
        reasons.append("股價在月線、季線之下，且月線低於季線（空頭排列）")
    elif last > ma60.iloc[-1]:
        score += 10
        reasons.append("股價在季線之上")
    else:
        score -= 10
        reasons.append("股價在季線之下")

    # 2. 均線交叉（5 日 vs 20 日）
    if _crossed_up(ma5, ma20):
        score += 20
        events.append("5 日線向上穿過 20 日線（黃金交叉）")
    elif _crossed_down(ma5, ma20):
        score -= 20
        events.append("5 日線向下跌破 20 日線（死亡交叉）")

    # 3. MACD
    if _crossed_up(m["dif"], m["dea"]):
        score += 15
        events.append("MACD 翻多（DIF 上穿訊號線）")
    elif _crossed_down(m["dif"], m["dea"]):
        score -= 15
        events.append("MACD 翻空（DIF 下穿訊號線）")
    elif m["hist"].iloc[-1] > 0:
        score += 5
        reasons.append("MACD 柱狀體為正（動能偏多）")
    else:
        score -= 5
        reasons.append("MACD 柱狀體為負（動能偏空）")

    # 4. RSI 過熱 / 超賣
    rv = float(r.iloc[-1])
    if rv >= 80:
        score -= 15
        reasons.append(f"RSI {rv:.0f}，短線過熱，追高風險大")
    elif rv <= 20:
        score += 15
        reasons.append(f"RSI {rv:.0f}，短線超賣，可能有反彈")
    elif rv >= 70:
        score -= 5
        reasons.append(f"RSI {rv:.0f}，偏熱")
    elif rv <= 30:
        score += 5
        reasons.append(f"RSI {rv:.0f}，偏冷")

    # 5. KD 低檔黃金交叉 / 高檔死亡交叉
    kv, dv = float(k["k"].iloc[-1]), float(k["d"].iloc[-1])
    if _crossed_up(k["k"], k["d"]) and kv < 30:
        score += 15
        events.append(f"KD 低檔黃金交叉（K={kv:.0f}）")
    elif _crossed_down(k["k"], k["d"]) and kv > 70:
        score -= 15
        events.append(f"KD 高檔死亡交叉（K={kv:.0f}）")

    # 6. 爆量
    if vol20.iloc[-1] and vol.iloc[-1] > 2 * vol20.iloc[-1]:
        chg = last / float(close.iloc[-2]) - 1
        if chg > 0:
            score += 10
            events.append(f"帶量上漲（成交量為 20 日均量 {vol.iloc[-1] / vol20.iloc[-1]:.1f} 倍）")
        elif chg < 0:
            score -= 10
            events.append(f"帶量下跌（成交量為 20 日均量 {vol.iloc[-1] / vol20.iloc[-1]:.1f} 倍）")

    score = max(-100, min(100, score))
    label = "偏多" if score >= 25 else "偏空" if score <= -25 else "中性"
    return SignalResult(
        score=score,
        label=label,
        reasons=reasons + events,
        events=events,
        indicators={
            "ma5": round(float(ma5.iloc[-1]), 2),
            "ma20": round(float(ma20.iloc[-1]), 2),
            "ma60": round(float(ma60.iloc[-1]), 2),
            "rsi": round(rv, 1),
            "k": round(kv, 1),
            "d": round(dv, 1),
            "macd_hist": round(float(m["hist"].iloc[-1]), 3),
        },
    )
