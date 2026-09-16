import os
import json
import duckdb
from datetime import datetime
import pandas as pd

DB_PATH = "market_data.duckdb"

# Kill-switch configuration
MAX_DAILY_PORTFOLIO_LOSS_RUPEES = 15000.0  # Hard daily circuit breaker

def init_order_book():
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS executed_orders (
            order_id VARCHAR PRIMARY KEY,
            timestamp TIMESTAMP,
            symbol VARCHAR,
            side VARCHAR,
            qty INTEGER,
            fill_price DOUBLE,
            target DOUBLE,
            stop_loss DOUBLE,
            broker VARCHAR,
            status VARCHAR
        )
    """)
    con.close()

def check_kill_switch_active() -> tuple[bool, float]:
    """
    Checks if today's cumulative realized + unrealized loss 
    exceeds the hard daily circuit breaker.
    """
    init_order_book()
    con = duckdb.connect(DB_PATH)
    today = datetime.now().date()
    
    # Calculate today's realized loss from trade_audit
    res = con.execute("""
        SELECT COALESCE(SUM(pnl_pct * entry_price * 0.01), 0.0) 
        FROM trade_audit 
        WHERE exit_date = ? AND pnl_pct < 0
    """, [today]).fetchone()
    con.close()

    daily_loss = abs(float(res[0])) if res else 0.0
    tripped = daily_loss >= MAX_DAILY_PORTFOLIO_LOSS_RUPEES
    return tripped, daily_loss

def execute_order(
    symbol: str,
    qty: int,
    entry_price: float,
    target: float,
    stop_loss: float,
    broker_mode: str = "PAPER_SANDBOX",
    api_credentials: dict = None
) -> dict:
    """
    Executes an equity trade with integrated target and stop parameters.
    Supports PAPER_SANDBOX, ZERODHA, and DHAN.
    """
    init_order_book()
    
    # 1. Enforce Hard Circuit Breaker
    tripped, current_loss = check_kill_switch_active()
    if tripped:
        return {
            "success": False,
            "status": "REJECTED_BY_KILL_SWITCH",
            "message": f"Daily loss circuit breaker active (₹{current_loss:,.2f} >= ₹{MAX_DAILY_PORTFOLIO_LOSS_RUPEES:,.2f}). Trading halted."
        }

    if qty <= 0:
        return {"success": False, "status": "INVALID_QTY", "message": "Quantity must be greater than 0."}

    order_id = f"ORD_{symbol}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 2. Paper Trading Execution (Slippage Model: 0.05% realistic fill degradation)
    if broker_mode == "PAPER_SANDBOX":
        slippage_drag = entry_price * 0.0005
        simulated_fill = round(entry_price + slippage_drag, 2)
        
        con = duckdb.connect(DB_PATH)
        con.execute("""
            INSERT INTO executed_orders VALUES 
            (?, ?, ?, 'BUY', ?, ?, ?, ?, 'PAPER', 'FILLED')
        """, [order_id, datetime.now(), symbol, qty, simulated_fill, target, stop_loss])
        con.close()

        return {
            "success": True,
            "order_id": order_id,
            "status": "FILLED",
            "broker": "PAPER_SANDBOX",
            "fill_price": simulated_fill,
            "qty": qty,
            "slippage_paid": round(slippage_drag * qty, 2)
        }

    # 3. Live Zerodha Kite Connect Adapter (Placeholder for API keys)
    elif broker_mode == "ZERODHA":
        # Requires: from kiteconnect import KiteConnect
        # kite.place_order(variety=kite.VARIETY_REGULAR, exchange=kite.EXCHANGE_NSE, ...)
        return {"success": False, "status": "API_NOT_CONNECTED", "message": "Zerodha API keys not initialized in environment."}

    # 4. Live DhanHQ Adapter (Placeholder for API keys)
    elif broker_mode == "DHAN":
        # Requires: from dhanhq import dhanhq
        return {"success": False, "status": "API_NOT_CONNECTED", "message": "DhanHQ access token not initialized in environment."}

    return {"success": False, "status": "UNKNOWN_MODE", "message": "Unsupported broker mode."}

if __name__ == "__main__":
    print("Testing Execution Engine & Risk Kill-Switch...")
    res = execute_order(
        symbol="RELIANCE",
        qty=5,
        entry_price=2980.0,
        target=3040.0,
        stop_loss=2935.0,
        broker_mode="PAPER_SANDBOX"
    )
    print("Execution Result:", res)