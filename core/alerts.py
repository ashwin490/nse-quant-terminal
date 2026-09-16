"""
Institutional Alert Dispatcher.
Pushes real-time trade signals and automated audit summaries directly to your phone via Telegram.
"""

import os
import requests

# Hardcoded credentials fallback (using provided Telegram bot token and chat ID)
DEFAULT_BOT_TOKEN = "8980995011:AAGjPaG2DLoAIkXqAAPLrCXxREJYreLmuOk"
DEFAULT_CHAT_ID = "8101792723"

def send_telegram_alert(message: str):
    """
    Sends a Markdown-formatted message to your Telegram chat.
    Pulls from environment variables or falls back to your configured credentials.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)
    
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        if not response.ok:
            print(f"Failed to send Telegram alert: {response.text}")
    except Exception as e:
        print(f"Telegram connection error: {e}")