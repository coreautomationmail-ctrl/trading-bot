# notifications.py
import os
from typing import Any, Dict, Optional

import requests


def send_telegram(text: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured:", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] error: {e}")


def send_telegram_photo(photo_path: str, caption: Optional[str] = None):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not os.path.isfile(photo_path):
        return
    try:
        with open(photo_path, "rb") as f:
            data: Dict[str, Any] = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "Markdown"
            requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data=data, files={"photo": f}, timeout=30,
            )
    except Exception as e:
        print(f"[telegram] photo error: {e}")
