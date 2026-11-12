"""讀 watchlist.yml（自選股 + 價格提醒），可直接在 GitHub 網頁 / 手機上編輯。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "watchlist.yml"
STATE = ROOT / "state" / "alerts_sent.json"
DOCS = ROOT / "docs"


@dataclass
class Stock:
    id: str
    note: str = ""
    above: float | None = None      # 漲到（含）這個價格就通知
    below: float | None = None      # 跌到（含）這個價格就通知
    change_pct: float | None = None  # 當日漲跌幅絕對值超過 N% 就通知
    yield_above: float | None = None  # ETF：殖利率（近一年配息 ÷ 現價）高於 N% 就通知（可考慮加碼）
    next_ex: str = ""                 # ETF：下次除息日（手動填，例如 2026-11-17）
    next_dps: float | None = None     # ETF：下次每股配息（手動填）


@dataclass
class Settings:
    stocks: list[Stock] = field(default_factory=list)
    signal_notify: bool = True      # 盤後是否推播「今天出現的買賣訊號」


def _num(v):
    if v in (None, ""):
        return None
    return float(v)


def load(path: Path = WATCHLIST) -> Settings:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stocks = []
    seen = set()
    for item in raw.get("stocks") or []:
        if isinstance(item, (str, int)):
            item = {"id": item}
        sid = str(item.get("id", "")).strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        alert = item.get("alert") or {}
        stocks.append(Stock(
            id=sid,
            note=str(item.get("note") or ""),
            above=_num(alert.get("above")),
            below=_num(alert.get("below")),
            change_pct=_num(alert.get("change_pct")),
            yield_above=_num(alert.get("yield_above")),
            next_ex=str(item.get("next_ex") or ""),
            next_dps=_num(item.get("next_dps")),
        ))
    return Settings(stocks=stocks, signal_notify=bool(raw.get("signal_notify", True)))


def load_state(path: Path = STATE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
