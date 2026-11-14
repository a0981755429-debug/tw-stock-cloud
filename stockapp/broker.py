"""（選用）從永豐金證券 Shioaji API 自動讀庫存，取代手動更新 HOLDINGS 的股數與成本。

只在 GitHub Secrets 有 SJ_API_KEY 與 SJ_SECRET_KEY 時才會啟用；沒設就完全照舊用 HOLDINGS。
只呼叫「查庫存」（list_positions），不下單、也不需要憑證。建議 API Key 只勾「帳務」＋「正式環境」，不要勾「交易」。

合併規則：
- 股數一律以永豐庫存為準（買賣後自動更新）。
- 成本：股數跟 HOLDINGS 一樣就沿用 HOLDINGS 的成本（含手續費較準），不一樣就用永豐的平均價 × 股數。
- 停損、停利、since、dividends、note 照 HOLDINGS 的設定。
- HOLDINGS 寫 `exclude: ["代號"]` 的股票（例如借名）不列入。
- HOLDINGS 有、永豐庫存沒有的 → 視為已賣出，不列入（會在 Telegram 私訊提醒）。

這個 repo 是公開的，Actions log 大家看得到，所以這裡的 log 一律不印代號、股數、金額。
"""
from __future__ import annotations

import logging
import os

import yaml

from stockapp.holdings import Holding

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(os.environ.get("SJ_API_KEY") and os.environ.get("SJ_SECRET_KEY"))


def excluded() -> set[str]:
    try:
        raw = yaml.safe_load(os.environ.get("HOLDINGS") or "") or {}
    except yaml.YAMLError:
        return set()
    ex = raw.get("exclude") if isinstance(raw, dict) else None
    return {str(x).strip() for x in (ex or [])}


def fetch_positions() -> dict[str, tuple[float, float]] | None:
    """回傳 {代號: (股數, 總成本)}；失敗回 None（不印細節，避免外洩）。"""
    try:
        import shioaji as sj
    except ImportError:
        log.warning("沒有安裝 shioaji，略過永豐庫存同步")
        return None
    api = sj.Shioaji(simulation=False)
    try:
        api.login(api_key=os.environ["SJ_API_KEY"], secret_key=os.environ["SJ_SECRET_KEY"],
                  subscribe_trade=False)
        unit = getattr(sj, "Unit", None) or __import__("shioaji.constant", fromlist=["Unit"]).Unit
        rows = api.list_positions(account=api.stock_account, unit=unit.Share)
    except Exception as e:  # noqa: BLE001
        log.warning("永豐庫存讀取失敗（%s），改用 HOLDINGS", type(e).__name__)
        return None
    finally:
        try:
            api.logout()
        except Exception:  # noqa: BLE001
            pass
    out: dict[str, list[float]] = {}
    for p in rows or []:
        if str(getattr(p, "direction", "Buy")).split(".")[-1] != "Buy":
            continue  # 融券放空不算持股
        qty, price = float(p.quantity or 0), float(p.price or 0)
        if qty <= 0:
            continue
        acc = out.setdefault(str(p.code).strip(), [0.0, 0.0])
        acc[0] += qty
        acc[1] += qty * price
    log.info("永豐庫存同步成功")
    return {k: (v[0], v[1]) for k, v in out.items()}


def merge(manual: list[Holding], positions: dict[str, tuple[float, float]],
          exclude: set[str]) -> tuple[list[Holding], list[str]]:
    """把永豐庫存和 HOLDINGS 合併，回傳 (持股, 給 Telegram 私訊的變動說明)。"""
    by_id = {h.id: h for h in manual}
    out, notes = [], []
    for sid, (shares, cost) in sorted(positions.items()):
        if sid in exclude:
            continue
        h = by_id.get(sid)
        if h is None:
            out.append(Holding(id=sid, shares=shares, cost=cost, stop_loss=None, take_profit=None))
            notes.append(f"{sid} 新增 {shares:,.0f} 股（均價 {cost / shares:,.2f}）")
            continue
        if abs(h.shares - shares) >= 1:
            notes.append(f"{sid} 股數 {h.shares:,.0f} → {shares:,.0f}")
            h = Holding(**{**h.__dict__, "shares": shares, "cost": cost})
        out.append(h)
    for h in manual:
        if h.id not in positions and h.id not in exclude:
            notes.append(f"{h.id} 永豐庫存裡沒有（已賣出？），先不列入")
    return out, notes


def sync(manual: list[Holding]) -> tuple[list[Holding], list[str]]:
    if not enabled():
        return manual, []
    pos = fetch_positions()
    if pos is None:
        return manual, ["⚠️ 永豐庫存讀取失敗，今天先用 HOLDINGS 的資料"]
    if not pos and manual:  # 帳戶沒簽署完成時也可能回空的，別把持股清光
        return manual, ["⚠️ 永豐庫存是空的，今天先用 HOLDINGS 的資料（全部賣光的話請清空 HOLDINGS）"]
    return merge(manual, pos, excluded())
