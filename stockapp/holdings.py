"""持股損益、資金配置與停損停利。

持股資料放在 GitHub Secret「HOLDINGS」（加密，別人看不到），內容是 YAML：

    holdings:
      - id: "2317"
        shares: 1000
        cost: 200000        # 總成本（含手續費），或改填 avg_cost
        stop_loss: 180      # 選填，不填 = 不提醒
        take_profit: 300    # 選填
        since: 2024-03-15   # 選填：第一次買進日，用來算「含息總報酬」（這天之後除息的配息才算你領到的）
        dividends: 12000    # 選填：已領配息總額（元）；有填就直接用，不用 since 推算

本機測試時也可以放在 repo 根目錄的 holdings.yml（已加進 .gitignore，不會上傳）。
公開網頁只會看到損益百分比與占比，金額、股數、成本只透過 Telegram 私訊。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from stockapp import insights
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
    since: str = ""              # 第一次買進日 YYYY-MM-DD（算含息報酬用）
    dividends: float | None = None  # 手填已領配息總額（元），優先於 since 推算

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
        sl, tp, dv = item.get("stop_loss"), item.get("take_profit"), item.get("dividends")
        try:
            dv = float(dv) if dv not in (None, "") else None
        except (TypeError, ValueError):
            dv = None
        out.append(Holding(
            id=sid, shares=shares, cost=cost, note=str(item.get("note") or ""),
            stop_loss=float(sl) if sl not in (None, "") else None,
            take_profit=float(tp) if tp not in (None, "") else None,
            since=str(item.get("since") or "")[:10], dividends=dv,
        ))
    return out


def portfolio(holdings: list[Holding], prices: dict[str, float], names: dict[str, str],
              div_events: dict[str, list[dict]] | None = None, today: date | None = None) -> dict:
    """持股摘要（含配息）。div_events = {代號: insights.dividend_events(...)}。"""
    today = today or date.today()
    div_events = div_events or {}
    rows = []
    for h in holdings:
        p = prices.get(h.id)
        value = p * h.shares if p is not None else None
        pl = value - h.cost if value is not None else None
        ev = div_events.get(h.id) or []
        # 已領配息：手填優先，其次用第一次買進日推算（假設這段期間股數不變，是估計值）
        if h.dividends is not None:
            div, n_div = h.dividends, None
        elif h.since:
            dps, n_div = insights.received(ev, h.since, today)
            div = dps * h.shares
        else:
            div, n_div = None, None
        tr = pl + div if pl is not None and div is not None else None
        ttm = insights.ttm_dps(ev, today)
        nxt = insights.next_dividend(ev, today)
        rows.append({
            "div_received": round(div) if div is not None else None, "div_count": n_div,
            "total_pl": round(tr) if tr is not None else None,
            "total_pl_pct": round(tr / h.cost * 100, 2) if tr is not None and h.cost else None,
            "annual_income": round(ttm * h.shares) if ttm else None,
            "yoc_pct": round(ttm / h.avg_cost * 100, 2) if ttm and h.avg_cost else None,
            "next_div": ({**nxt, "amount": round(nxt["dps"] * h.shares)} if nxt else None),
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
    # 含息總報酬：只有全部持股都算得出配息時才給整體數字，避免缺一檔造成誤導
    priced = [r for r in rows if r["value"] is not None]
    all_div = bool(priced) and all(r["total_pl"] is not None for r in priced)
    total_div = sum(r["div_received"] for r in priced) if all_div else None
    income = sum(r["annual_income"] or 0 for r in rows)
    return {
        "rows": sorted(rows, key=lambda r: -(r["value"] or 0)),
        "total_value": round(total_v), "total_cost": round(total_c),
        "total_pl": round(total_v - total_c),
        "total_pl_pct": round((total_v - total_c) / total_c * 100, 2) if total_c else None,
        "total_div": total_div,
        "total_return_pct": round((total_v - total_c + total_div) / total_c * 100, 2) if all_div and total_c else None,
        "annual_income": income or None,
        "income_yield_pct": round(income / total_v * 100, 2) if income and total_v else None,
        "warnings": warnings,
    }


def public_view(pf: dict) -> dict:
    """公開網頁用：只留百分比，拿掉金額、股數、成本、均價與停損停利價。"""
    return {
        "rows": [{"id": r["id"], "name": r["name"], "price": r["price"],
                  "pl_pct": r["pl_pct"], "total_pl_pct": r["total_pl_pct"], "yoc_pct": r["yoc_pct"],
                  "weight": r["weight"], "next_ex": (r["next_div"] or {}).get("ex")} for r in pf["rows"]],
        "total_pl_pct": pf["total_pl_pct"],
        "total_return_pct": pf["total_return_pct"],
        "income_yield_pct": pf["income_yield_pct"],
        "warnings": pf["warnings"],
    }


def summary_text(pf: dict) -> str:
    lines = ["💼 持股損益（私訊）"]
    for r in pf["rows"]:
        if r["value"] is None:
            continue
        lines.append(f"{r['id']} {r['name']} {r['shares']:,.0f} 股｜市值 {r['value']:,}｜{r['pl']:+,}（{r['pl_pct']:+.1f}%）｜占 {r['weight']:.0f}%")
        if r["div_received"] is not None:
            lines.append(f"   已領息 {r['div_received']:,}｜含息 {r['total_pl']:+,}（{r['total_pl_pct']:+.1f}%）"
                         + (f"｜成本殖利率 {r['yoc_pct']:.1f}%" if r["yoc_pct"] else ""))
    if pf["total_cost"]:
        lines.append(f"合計 {pf['total_value']:,}，損益 {pf['total_pl']:+,}（{pf['total_pl_pct']:+.1f}%）")
        if pf["total_return_pct"] is not None:
            lines.append(f"含息總報酬 {pf['total_pl'] + pf['total_div']:+,}（{pf['total_return_pct']:+.1f}%），累計領息 {pf['total_div']:,}")
        if pf["annual_income"]:
            lines.append(f"預估一年領息 {pf['annual_income']:,}（月均 {pf['annual_income'] // 12:,}，以近一年配息推估）")
    lines += [f"⚠️ {w}" for w in pf["warnings"]]
    return "\n".join(lines)


def dividend_reminders(pf: dict | None, items: list[dict], today: date, days: int = 7) -> str:
    """除息日 N 天內的提醒：持股附上預計領多少（Telegram 私訊），自選股只列日期與每股金額。"""
    end = (today + timedelta(days=days)).isoformat()
    t = today.isoformat()
    lines, seen = [], set()
    for r in (pf or {}).get("rows", []):
        n = r.get("next_div")
        if n and t <= n["ex"] <= end:
            seen.add(r["id"])
            lines.append(f"{r['id']} {r['name']} {n['ex'][5:]} 除息，每股 {n['dps']:g} 元，"
                         f"你 {r['shares']:,.0f} 股預計領 {n['amount']:,} 元" + (f"（{n['pay'][5:]} 入帳）" if n.get("pay") else ""))
    for it in items:
        n = None
        etf = it["signal"].get("etf")
        if etf and etf.get("next_src") == "自動":
            n = {"ex": etf["next_ex"], "dps": etf["next_dps"]}
        elif (it.get("fundamentals") or {}).get("next_dividend"):
            n = it["fundamentals"]["next_dividend"]
        if n and it["id"] not in seen and t <= n["ex"] <= end:
            lines.append(f"{it['id']} {it['name']} {n['ex'][5:]} 除息，每股 {n['dps']:g} 元")
    if not lines:
        return ""
    return "📅 即將除息（除息日前一個交易日收盤前持有才領得到）\n" + "\n".join(lines)


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
