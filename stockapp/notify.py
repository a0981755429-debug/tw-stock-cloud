"""Telegram 推播。在 GitHub → Settings → Secrets 設 TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID。"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


def telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("未設定 Telegram，略過推播（內容可能含持股金額，不印在公開 log）")
        return False
    ok = True
    for i in range(0, len(text), 4000):  # Telegram 單則上限 4096 字
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[i:i + 4000], "disable_web_page_preview": True},
                timeout=15,
            )
            if r.status_code != 200:
                log.warning("Telegram 回 %s: %s", r.status_code, r.text[:200])
                ok = False
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram 推播失敗: %s", type(e).__name__)  # 不印原文，避免 token 外洩到 log
            ok = False
    return ok
