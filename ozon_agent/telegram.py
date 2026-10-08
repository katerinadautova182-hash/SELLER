"""Telegram notifications for Ozon agent.

Secrets:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import os
from pathlib import Path
import requests


def _credentials() -> tuple[str, str]:
    return (
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    )


def send_telegram(text: str) -> bool:
    token, chat_id = _credentials()
    if not token or not chat_id:
        print("Telegram not configured; skipping notification.")
        return False

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = {"status_code": response.status_code, "text": response.text[:500]}
        print(f"Telegram API error: {detail}")
        response.raise_for_status()
    print("Telegram notification sent.")
    return True


def send_telegram_document(path: str | Path, caption: str = "") -> bool:
    token, chat_id = _credentials()
    if not token or not chat_id:
        print("Telegram not configured; skipping document.")
        return False

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    with p.open("rb") as fh:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat_id, "caption": caption[:1024]},
            files={"document": (p.name, fh)},
            timeout=60,
        )
    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = {"status_code": response.status_code, "text": response.text[:500]}
        print(f"Telegram API error: {detail}")
        response.raise_for_status()
    print("Telegram document sent.")
    return True
