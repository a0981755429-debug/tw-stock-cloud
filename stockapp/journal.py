"""交易紀錄的 Telegram 檢討提醒。

交易紀錄由網頁 docs/journal/ 加密後存在 docs/journal/vault.json（PBKDF2-SHA256 → AES-GCM，
跟財務規劃台同一種）。要讓盤後推播附上「該檢討的」和「跌破停損的」，到 GitHub Secrets
新增 JOURNAL_PASS（值 = 網頁上的密碼）。沒設就略過。

內容只會出現在 Telegram 私訊，不會寫進 docs/data.json，也不印到公開的 Actions log。
"""
from __future__ import annotations

import base64
import json
import logging
import os
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

log = logging.getLogger(__name__)
VAULT = Path(__file__).resolve().parent.parent / "docs" / "journal" / "vault.json"


def decrypt(vault: dict, password: str) -> dict:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=base64.b64decode(vault["salt"]), iterations=int(vault["iter"]))
    key = kdf.derive(password.encode())
    pt = AESGCM(key).decrypt(base64.b64decode(vault["iv"]), base64.b64decode(vault["ct"]), None)
    return json.loads(pt)


def load(path: Path = VAULT) -> dict | None:
    password = os.environ.get("JOURNAL_PASS")
    if not password or not path.exists():
        return None
    try:
        return decrypt(json.loads(path.read_text(encoding="utf-8")), password)
    except Exception as e:  # noqa: BLE001
        log.warning("交易紀錄解不開（JOURNAL_PASS 不對或檔案損壞）：%s", type(e).__name__)
        return None


def shares_held(t: dict) -> float:
    sh = 0.0
    for f in sorted(t.get("fills") or [], key=lambda f: f.get("date") or ""):
        n = float(f.get("shares") or 0)
        sh = sh + n if f.get("side") == "買" else max(0.0, sh - n)
    return sh


def status(t: dict) -> str:
    if t.get("dropped"):
        return "放棄"
    if not t.get("fills"):
        return "想法"
    return "持有" if shares_held(t) > 0 else "結案"


def to_watch(data: dict) -> list[str]:
    """回傳需要另外抓價格的代號（持有中、有停損價）。"""
    return sorted({t["sid"] for t in data.get("trades") or []
                   if status(t) == "持有" and t.get("stop") is not None})


def reminders(data: dict, prices: dict[str, float], today: date) -> str:
    """產生 Telegram 訊息；沒有要提醒的就回傳空字串。"""
    ts = today.isoformat()
    due, broke = [], []
    for t in data.get("trades") or []:
        st = status(t)
        if st in ("結案", "放棄"):
            continue
        label = f"{t.get('sid')} {t.get('name') or ''}".strip()
        if t.get("nextReview") and t["nextReview"] <= ts:
            first = next((f.get("date") for f in sorted(t.get("fills") or [], key=lambda f: f.get("date") or "")
                          if f.get("side") == "買"), None)
            held = f"持有 {(today - date.fromisoformat(first)).days} 天" if first else "還是想法"
            why = (t.get("thesis") or "").replace("\n", " ")
            why = why[:40] + ("…" if len(why) > 40 else "")
            due.append(f"• {label}（{held}）理由：{why or '—'}")
        p, stop = prices.get(t.get("sid")), t.get("stop")
        if st == "持有" and stop is not None and p is not None and p <= float(stop):
            broke.append(f"• {label} 現價 {p:g} ≤ 停損 {float(stop):g}，出場條件：{(t.get('wrongIf') or '—')[:40]}")
    if not due and not broke:
        return ""
    lines = ["📓 交易紀錄"]
    if broke:
        lines += ["跌破你寫的停損（照計畫出場，或寫下為什麼要改）："] + broke
    if due:
        lines += ["今天該檢討（買進理由還成立嗎？）："] + due
    return "\n".join(lines)
