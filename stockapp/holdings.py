"""持股損益、資金配置與停損停利。

持股資料放在 GitHub Secret「HOLDINGS」（加密，別人看不到），內容是 YAML：

    holdings:
      - id: "2317"
        shares: 550
        cost: 134099        # 總成本（含手續費），或改填 avg_cost
        stop_loss: 216      # 選填，不填 = 不提醒
        take_profit: 300    # 選填

本機測試時也可以放在 repo 根目錄的 holdings.yml（已加進 .gitignore，不會上傳）。
公開網頁只會看到損益百分比與占比，金額、股數、成本只透過 Telegram 私訊。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from stockapp.data import Quote

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
HOLDINGS = ROOT / "holdings.yml"



@dataclass
class Holding:
    id: str
    shares: float
    cost: float               # 總成本（含手續費）
    stop_loss: float | None     # 沒填 = 不提醒
    take_profit: float | None
    note: str = ""

    @property
    def avg_cost(self) -> float:
        return self.cost / self.shares if self.shares else 0.0


def load(path: Path = HOLDINGS) -> list[Holding]:
    text = os.environ.get("HOLDINGS")
    if not text:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        log.warning("持股資料不是正確的 YAML 格式，已略過")  # 不印內容，避免外洩到公開 log
        return []
    if isinstance(raw, list):
        raw = {"holdings": raw}
    out = []
    for item in raw.get("holdings") or []:
        try:
            sid = str(item["id"]).strip()
            shares = float(item["shares"])
            if shares <= 0:
                continue
            cost = float(item["cost"]) if item.get("cost") is not None else float(item["avg_cost"]) * shares
        except (KeyError, TypeError, ValueError):
            log.warning("持股資料有一筆格式不對（需要 id、shares、cost 或 avg_cost），已略過")
            continue
        sl, tp = item.get("stop_loss"), item.get("take_profit")
        out.append(Holding(
            id=sid, shares=shares, cost=cost, note=str(item.get("note") or ""),
            stop_loss=float(sl) if sl not in (None, "") else None,
            take_profit=float(tp) if tp not in (None, "") else None,
        ))
    return out


def portfolio(holdings: list[Holding], prices: dict[str, float], names: dict[str, str]) -> dict:
    """給網頁用的持股摘要。"""
    rows = []
    for h in holdings:
        p = prices.get(h.id)
        value = p * h.shares if p is not None else None
        pl = value - h.cost if value is not None else None
        rows.append({
            "id": h.id, "name": names.get(h.id, h.id), "shares": h.shares, "cost": round(h.cost),
            "avg_cost": round(h.avg_cost, 2), "price": p,
            "value": round(value) if value is not None else None,
            "pl": round(pl) if pl is not None else None,
            "pl_pct": round(pl / h.cost * 100, 2) if pl is not None and h.cost else None,
            "stop_loss": h.stop_loss, "take_profit": h.take_profit, "note": h.note,
        })
    total_v = sum(r["value"] for r in rows if r["value"] is not None)
    total_c = sum(r["cost"] for r in rows if r["value"] is not None)
    for r in rows:
        r["weight"] = round(r["value"] / total_v * 100, 1) if total_v and r["value"] is not None else None
    # ETF（代號 00 開頭）本身已分散，只對個股提醒集中度
    warnings = [f"{r['name']} 占持股 {r['weight']:.0f}%，單一個股集中度偏高"
                for r in rows if (r["weight"] or 0) > 40 and not r["id"].startswith("00")]
    return {
        "rows": sorted(rows, key=lambda r: -(r["value"] or 0)),
        "total_value": round(total_v), "total_cost": round(total_c),
        "total_pl": round(total_v - total_c),
        "total_pl_pct": round((total_v - total_c) / total_c * 100, 2) if total_c else None,
        "warnings": warnings,
    }


def public_view(pf: dict) -> dict:
    """公開網頁用：只留百分比，拿掉金額、股數、成本、均價與停損停利價。"""
    return {
        "rows": [{"id": r["id"], "name": r["name"], "price": r["price"],
                  "pl_pct": r["pl_pct"], "weight": r["weight"]} for r in pf["rows"]],
        "total_pl_pct": pf["total_pl_pct"],
        "warnings": pf["warnings"],
    }


def summary_text(pf: dict) -> str:
    lines = ["💼 持股損益（私訊）"]
    for r in pf["rows"]:
        if r["value"] is None:
            continue
        lines.append(f"{r['id']} {r['name']} {r['shares']:,.0f} 股｜市值 {r['value']:,}｜{r['pl']:+,}（{r['pl_pct']:+.1f}%）｜占 {r['weight']:.0f}%")
    if pf["total_cost"]:
        lines.append(f"合計 {pf['total_value']:,}，損益 {pf['total_pl']:+,}（{pf['total_pl_pct']:+.1f}%）")
    lines += [f"⚠️ {w}" for w in pf["warnings"]]
    return "\n".join(lines)


def check_stops(holdings: list[Holding], quotes: dict[str, Quote], names: dict[str, str],
                state: dict, today: str) -> list[str]:
    """停損 / 停利到價，與價格提醒共用 state：每天每檔每種只通知一次。"""
    sent = set(state.get(today, []))
    msgs = []
    for h in holdings:
        q = quotes.get(h.id)
        if q is None or not q.is_live:
            continue
        name = names.get(h.id) or q.name or h.id
        pct = (q.price / h.avg_cost - 1) * 100 if h.avg_cost else 0
        hits = []
        if h.stop_loss is not None and q.price <= h.stop_loss:
            hits.append(("stop", f"🛑 停損提醒：{h.id} {name} 現價 {q.price:g}，跌破停損 {h.stop_loss:g}（相對均價 {pct:+.1f}%）"))
        if h.take_profit is not None and q.price >= h.take_profit:
            hits.append(("take", f"💰 停利提醒：{h.id} {name} 現價 {q.price:g}，達到停利 {h.take_profit:g}（相對均價 {pct:+.1f}%）"))
        for kind, msg in hits:
            key = f"H:{h.id}:{kind}"
            if key not in sent:
                sent.add(key)
                msgs.append(msg)
    if sent:
        state[today] = sorted(sent)
    return msgs
