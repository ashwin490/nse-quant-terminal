import duckdb
import pandas as pd
from datetime import datetime
import yfinance as yf
from core.trailing_stops import calculate_dynamic_stop
from core.self_learner import perform_trade_autopsy

DB_PATH = "market_data.duckdb"

def init_audit_table():
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_audit (
            prediction_id VARCHAR PRIMARY KEY,
            symbol VARCHAR,
            prediction_date DATE,
            entry_price DOUBLE,
            target_price DOUBLE,
            initial_stop DOUBLE,
            trailing_stop DOUBLE,
            highest_price DOUBLE,
            predicted_prob VARCHAR,
            status VARCHAR,
            exit_price DOUBLE,
            exit_date DATE,
            pnl_pct DOUBLE,
            stop_stage VARCHAR
        )
    """)
    con.close()

def log_predictions(df_top_trades: pd.DataFrame):
    init_audit_table()
    con = duckdb.connect(DB_PATH)
    today = datetime.now().date()

    for _, row in df_top_trades.iterrows():
        sym = str(row["Ticker"])
        pred_id = f"{sym}_{today.strftime('%Y%m%d')}"
        
        entry = float(row.get("Price (₹)", row.get("Price", 0.0)))
        target = float(row.get("Target (₹)", row.get("Target", 0.0)))
        stop = float(row.get("Stop Loss (₹)", row.get("Stop Loss", 0.0)))
        
        # Defensive check against schema key renaming
        prob = str(row.get("AI Win Confidence", row.get("ML Probability", "60.0%")))

        exists = con.execute("SELECT COUNT(*) FROM trade_audit WHERE prediction_id = ?", [pred_id]).fetchone()[0]
        if exists == 0:
            con.execute("""
                INSERT INTO trade_audit VALUES 
                (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', NULL, NULL, NULL, 'INITIAL')
            """, [pred_id, sym, today, entry, target, stop, stop, entry, prob])

    con.close()

def evaluate_pending_trades():
    """Monitors active positions, updates trailing stops, and routes failures to autopsy."""
    init_audit_table()
    con = duckdb.connect(DB_PATH)
    open_trades = con.execute("SELECT * FROM trade_audit WHERE status = 'OPEN'").fetchdf()

    if open_trades.empty:
        con.close()
        return []

    updated_events = []

    for _, trade in open_trades.iterrows():
        pred_id = trade["prediction_id"]
        sym = f"{trade['symbol']}.NS"
        clean_sym = trade['symbol']
        entry = float(trade["entry_price"])
        target = float(trade["target_price"])
        initial_stop = float(trade["initial_stop"])
        current_trailing = float(trade["trailing_stop"])
        highest_so_far = float(trade["highest_price"])

        try:
            df_hist = yf.download(sym, period="5d", interval="1m", progress=False)
            if df_hist.empty:
                df_hist = yf.download(sym, period="5d", interval="1d", progress=False)

            if df_hist.empty:
                continue
            if isinstance(df_hist.columns, pd.MultiIndex):
                df_hist.columns = [c[0] for c in df_hist.columns]

            curr_price = float(df_hist["Close"].iloc[-1])
            recent_high = float(df_hist["High"].max())
            recent_low = float(df_hist["Low"].iloc[-1])
            atr_est = abs(target - entry) / 1.5

            highest_price = max(highest_so_far, recent_high)

            stop_info = calculate_dynamic_stop(
                entry_price=entry,
                current_high=highest_price,
                current_price=curr_price,
                atr=atr_est,
                initial_stop=initial_stop
            )
            new_stop = max(current_trailing, stop_info["current_stop"])
            stage = stop_info["stage"]

            # Target Hit
            if curr_price >= target:
                pnl = round(((target - entry) / entry * 100.0) - 0.15, 2)
                con.execute("""
                    UPDATE trade_audit 
                    SET status = 'TARGET_HIT', exit_price = ?, exit_date = ?, pnl_pct = ?, trailing_stop = ?, stop_stage = ?
                    WHERE prediction_id = ?
                """, [target, datetime.now().date(), pnl, new_stop, stage, pred_id])
                updated_events.append({"symbol": clean_sym, "event": "TARGET_HIT", "pnl": pnl})

            # Stop Invalidation -> Execute Autopsy
            elif curr_price <= new_stop or recent_low <= new_stop:
                pnl = round(((new_stop - entry) / entry * 100.0) - 0.15, 2)
                con.execute("""
                    UPDATE trade_audit 
                    SET status = 'STOPPED_OUT', exit_price = ?, exit_date = ?, pnl_pct = ?, trailing_stop = ?, stop_stage = ?
                    WHERE prediction_id = ?
                """, [new_stop, datetime.now().date(), pnl, new_stop, stage, pred_id])

                autopsy = perform_trade_autopsy(clean_sym, pred_id, pnl)
                updated_events.append({
                    "symbol": clean_sym,
                    "event": "STOPPED_OUT",
                    "pnl": pnl,
                    "reason": autopsy["factor"],
                    "explanation": autopsy["explanation"]
                })
            else:
                con.execute("""
                    UPDATE trade_audit 
                    SET highest_price = ?, trailing_stop = ?, stop_stage = ?
                    WHERE prediction_id = ?
                """, [highest_price, new_stop, stage, pred_id])
        except Exception:
            continue

    con.close()
    return updated_events

def get_audit_summary() -> dict:
    init_audit_table()
    con = duckdb.connect(DB_PATH)
    df = con.execute("SELECT * FROM trade_audit ORDER BY prediction_date DESC").fetchdf()
    con.close()

    if df.empty:
        return {"win_rate": 0.0, "total": 0, "closed": 0, "wins": 0, "losses": 0, "avg_pnl": 0.0, "records": pd.DataFrame()}

    closed = df[df["status"].isin(["TARGET_HIT", "STOPPED_OUT"])]
    wins = len(closed[closed["pnl_pct"] > 0])
    losses = len(closed[closed["pnl_pct"] <= 0])
    total_closed = len(closed)
    win_rate = round((wins / total_closed) * 100.0, 1) if total_closed > 0 else 0.0
    avg_pnl = round(float(closed["pnl_pct"].mean()), 2) if total_closed > 0 else 0.0

    return {
        "win_rate": win_rate,
        "total": len(df),
        "closed": total_closed,
        "wins": wins,
        "losses": losses,
        "avg_pnl": avg_pnl,
        "records": df
    }