"""Real-time visibility into what Psylocke 1 and 2 are doing, via Telegram.

This is deliberately decoupled from the signals table: it's a one-way
broadcast each bot does independently after acting, not a control channel.
Neither bot's logic depends on a message actually being delivered -- if
TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID aren't set, or a send fails, trading
continues unaffected and it's just logged.
"""
import logging

import requests

logger = logging.getLogger("psylocke.notify")


def send(config, text: str) -> None:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": config.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        logger.exception("failed to send Telegram notification")
