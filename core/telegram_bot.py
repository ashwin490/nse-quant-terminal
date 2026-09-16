import urllib.request
import urllib.parse
import json

TELEGRAM_BOT_TOKEN = "8980995011:AAGjPaG2DLoAIkXqAAPLrCXxREJYreLmuOk"
TELEGRAM_CHAT_ID = "8101792723"

def send_telegram_card(trade_data: dict, regime_data: dict) -> bool:
    """
    Dispatches a single formatted institutional trade card to your Telegram.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Credentials not configured. Skipping mobile alert.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # HTML formatted message card
    text = (
        f"<b>⚡ NSE PRE-MARKET QUANT ALERT</b>\n"
        f"<i>Regime: {regime_data.get('regime', 'Neutral')} | VIX: {regime_data.get('vix', 0.0)}</i>\n\n"
        f"📈 <b>Ticker:</b> <code>{trade_data['Ticker']}</code> ({trade_data.get('Filing Status', 'NEUTRAL')})\n"
        f"🎯 <b>Win Probability:</b> <b>{trade_data['ML Probability']}</b>\n"
        f"💵 <b>CMP:</b> ₹{trade_data['Price']} | <b>ATR:</b> ₹{trade_data.get('ATR', 'N/A')}\n\n"
        f"📦 <b>Delivery Accumulation:</b> {trade_data.get('Delivery %', 'N/A')} ({trade_data.get('Deliv Shock', '1.0')}x)\n"
        f"🛒 <b>Allocation:</b> <code>{trade_data['Qty to Buy']} shares</code> (₹{trade_data['Capital (₹)']:,})\n"
        f"🛑 <b>Max Risk:</b> ₹{trade_data['Max Risk (₹)']:,} ({trade_data.get('Port Weight', '0%')} port)\n\n"
        f"🎯 <b>Target (+1.5 ATR):</b> ₹{trade_data['Target']}\n"
        f"🛑 <b>Stop Loss (-1.0 ATR):</b> ₹{trade_data['Stop Loss']}\n"
    )

    if trade_data.get("Latest Headline"):
        text += f"\n📰 <b>Disclosure:</b> <i>{trade_data['Latest Headline'][:100]}...</i>"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[TELEGRAM ERROR] Failed to send alert: {e}")
        return False

def dispatch_batch_alerts(top_trades: list, regime_data: dict):
    """Dispatches each setup in the top tier as an individual card."""
    for trade in top_trades:
        send_telegram_card(trade, regime_data)

if __name__ == "__main__":
    print("Testing Telegram connection to your device...")
    sample_trade = {
        "Ticker": "RELIANCE",
        "Price": 2980.50,
        "ATR": 42.10,
        "ML Probability": "74.8%",
        "Filing Status": "CATALYST",
        "Qty to Buy": 25,
        "Capital (₹)": 74512,
        "Max Risk (₹)": 1450,
        "Port Weight": "14.9%",
        "Delivery %": "62.4%",
        "Deliv Shock": "1.85",
        "Target": 3055.00,
        "Stop Loss": 2930.00,
        "Latest Headline": "Reliance Retail announces strategic expansion in high-growth segments."
    }
    sample_regime = {"regime": "BULLISH_EXPANSION", "vix": 13.8}
    success = send_telegram_card(sample_trade, sample_regime)
    if success:
        print("✓ Success! Check Telegram on your phone.")
    else:
        print("✗ Failed. Make sure you tapped 'Start' on the bot in Telegram first.")