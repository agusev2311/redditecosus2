from __future__ import annotations

import httpx

from app.config import settings


def send_telegram_alert(message: str) -> bool:
    chat_id = settings.telegram_alert_chat_id or settings.telegram_backup_chat_id
    if not settings.telegram_bot_token or not chat_id:
        return False

    with httpx.Client(timeout=20) as client:
        response = client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            data={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            },
        )
        response.raise_for_status()
    return True
