"""
Trade Guardian Autonomous Daemon.
Performs 5-minute intraday price checks and daily EOD outcome verification,
updating AI memory on wins or triggering root-cause autopsies on stop-losses.
"""

import time
import duckdb
import yfinance as yf
from datetime import datetime
from core.self_learner import log_trade_autopsy

def evaluate_pending_trades():
    """
    Evaluates active trades in trade_audit against current market prices.
    If target hit -> marks Win. If stop-loss hit -> triggers autonomous autopsy & punishment penalty.
    """
    con = duckdb.connect("market_data.duckdb")
    try:
        active_trades = con.execute("""
            SELECT id, symbol, entry_price, target_price, initial_stop, entry_date 
            FROM trade_audit 
            WHERE status = 'ACTIVE'
        %s""").df()
    except Exception:
        con.close()
        return

    if active_trades.empty:
        con.close()
        return

    symbols = [f"{sym}.NS" for sym in active_trades["symbol"].tolist()]
    data = yf.download(symbols, period="2d", interval="1d", group_by="ticker", progress=False)

    for _, row in active_trades.iterrows():
        sym = row["symbol"]
        trade_id = row["id"]
        entry = row["entry_price"]
        target = row["target_price"]
        stop = row["initial_stop"]

        try:
            df_sym = data[f"{sym}.NS"] if isinstance(data.columns, pd.MultiIndex) else data
            if df_sym.empty:
                continue
            current_close = float(df_sym["Close"].iloc[-1])
            current_high = float(df_sym["High"].iloc[-1])
            current_low = float(df_sym["Low"].iloc[-1])

            if current_high >= target:
                # Target Reached -> Autonomous Win Memory Update
                con.execute("""
                    UPDATE trade_audit 
                    SET status = 'WIN', exit_date = ?, exit_price = ?, pnl_pct = ? 
                    WHERE id = ?
                """, [datetime.now().date(), target, round(((target - entry)/entry)*100, 2), trade_id])
                print(f"[TRADE GUARDIAN] 🎯 TARGET REACHED for {sym}! Memory updated to WIN.")

            elif current_low <= stop:
                # Stop Loss Hit -> Autonomous Failure Autopsy & Learning
                pnl = round(((stop - entry)/entry)*100, 2)
                con.execute("""
                    UPDATE trade_audit 
                    SET status = 'LOSS', exit_date = ?, exit_price = ?, pnl_pct = ? 
                    WHERE id = ?
                """, [datetime.now().date(), stop, pnl, trade_id])
                
                # Trigger automated self-learning autopsy
                log_trade_autopsy(trade_id, sym, pnl, "Stop-Loss Breach (Autonomous 10-Yr Audit)")
                print(f"[TRADE GUARDIAN] 🛑 STOP LOSS HIT for {sym}. Autopsy logged & penalty weights adjusted.")
        except Exception as e:
            continue

    con.close()

def run_guardian_daemon(interval_seconds=300):
    """
    Background daemon running every 5 minutes (300 seconds) for real-time tracking.
    """
    print(f"⚡ Trade Guardian daemon started. Monitoring trades every {interval_seconds} seconds...")
    while True:
        try:
            evaluate_pending_trades()
        except Exception as e:
            print(f"Guardian error: {e}")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    run_guardian_daemon()