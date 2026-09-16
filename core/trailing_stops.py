def calculate_dynamic_stop(
    entry_price: float,
    current_high: float,
    current_price: float,
    atr: float,
    initial_stop: float
) -> dict:
    """
    Implements a 3-tier dynamic volatility trailing stop:
    - Stage 1: Initial stop (-1.0 ATR)
    - Stage 2: Breakeven trigger when price reaches +0.75 ATR
    - Stage 3: Dynamic profit trail when price exceeds +1.2 ATR
    """
    if atr <= 0 or entry_price <= 0:
        return {"current_stop": initial_stop, "stage": "INITIAL", "pnl_locked_pct": 0.0}

    gain = current_high - entry_price
    atr_progress = gain / atr

    # Stage 3: Deep in profit (> 1.2 ATR gain) -> Trail 0.8 ATR below highest high
    if atr_progress >= 1.2:
        trail_stop = current_high - (0.8 * atr)
        effective_stop = max(initial_stop, entry_price + (0.25 * atr), trail_stop)
        stage = "PROFIT_TRAIL"
    
    # Stage 2: Reached +0.75 ATR target -> Ratchet to breakeven + minimal fee buffer
    elif atr_progress >= 0.75:
        fee_buffer = entry_price * 0.0015  # 0.15% STT & exchange fee buffer
        effective_stop = max(initial_stop, entry_price + fee_buffer)
        stage = "BREAKEVEN"
    
    # Stage 1: Normal initial stop
    else:
        effective_stop = initial_stop
        stage = "INITIAL"

    locked_pnl = round(((effective_stop - entry_price) / entry_price) * 100.0, 2)

    return {
        "current_stop": round(effective_stop, 2),
        "stage": stage,
        "pnl_locked_pct": locked_pnl
    }

if __name__ == "__main__":
    print("Testing Dynamic Trailing Stop Engine...")
    entry = 1000.0
    atr_val = 20.0
    init_stop = 980.0

    # Scenario A: Early in trade
    s1 = calculate_dynamic_stop(entry, current_high=1010.0, current_price=1008.0, atr=atr_val, initial_stop=init_stop)
    print(f"Price ₹1008 (+0.50 ATR): Stop = ₹{s1['current_stop']} | Stage = {s1['stage']}")

    # Scenario B: Breakeven triggered (+0.80 ATR)
    s2 = calculate_dynamic_stop(entry, current_high=1016.0, current_price=1015.0, atr=atr_val, initial_stop=init_stop)
    print(f"Price ₹1015 (+0.80 ATR): Stop = ₹{s2['current_stop']} | Stage = {s2['stage']}")

    # Scenario C: Deep in money (+1.60 ATR)
    s3 = calculate_dynamic_stop(entry, current_high=1032.0, current_price=1028.0, atr=atr_val, initial_stop=init_stop)
    print(f"Price ₹1028 (+1.60 ATR): Stop = ₹{s3['current_stop']} | Stage = {s3['stage']} (Locked: {s3['pnl_locked_pct']}%)")