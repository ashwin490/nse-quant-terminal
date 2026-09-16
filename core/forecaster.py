import pandas as pd
import numpy as np
from datetime import timedelta

def generate_forecast_cone(df_chart: pd.DataFrame, days_ahead: int = 5, multiplier: float = 1.0) -> pd.DataFrame:
    """
    Computes a forward-looking empirical volatility projection cone 
    anchored to the last close, scaling by ATR and historical daily volatility.
    """
    if df_chart.empty or len(df_chart) < 14:
        return pd.DataFrame()

    last_row = df_chart.iloc[-1]
    last_date = df_chart.index[-1]
    last_close = float(last_row["Close"])

    # Compute 14-day True Range
    high_low = df_chart["High"] - df_chart["Low"]
    high_prev = (df_chart["High"] - df_chart["Close"].shift(1)).abs()
    low_prev = (df_chart["Low"] - df_chart["Close"].shift(1)).abs()
    tr = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
    daily_atr = float(tr.rolling(14).mean().iloc[-1])

    # Momentum drift from 20-day EMA slope
    ema20 = df_chart["Close"].ewm(span=20, adjust=False).mean()
    momentum_slope = float((ema20.iloc[-1] - ema20.iloc[-5]) / 5.0) if len(ema20) >= 5 else 0.0

    # Project forward dates skipping weekends
    forecast_dates = [last_date]
    curr_date = last_date
    while len(forecast_dates) <= days_ahead:
        curr_date += timedelta(days=1)
        if curr_date.weekday() < 5:  # 0-4 are Mon-Fri
            forecast_dates.append(curr_date)

    forecast_dates = forecast_dates[1:]  # Drop anchor date

    upper_path = []
    median_path = []
    lower_path = []

    # Expanding square-root-of-time volatility cone
    for step in range(1, len(forecast_dates) + 1):
        drift = momentum_slope * step * 0.5
        expanded_vol = daily_atr * np.sqrt(step) * multiplier

        upper = last_close + drift + (1.5 * expanded_vol)
        median = last_close + drift
        lower = last_close + drift - (1.0 * expanded_vol)

        upper_path.append(round(upper, 2))
        median_path.append(round(median, 2))
        lower_path.append(round(lower, 2))

    return pd.DataFrame({
        "Date": forecast_dates,
        "Upper_Target": upper_path,
        "Median_Path": median_path,
        "Lower_Stop": lower_path
    }).set_index("Date")

if __name__ == "__main__":
    import yfinance as yf
    print("Testing forecast cone on RELIANCE...")
    data = yf.download("RELIANCE.NS", period="30d", interval="1d", progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = [c[0] for c in data.columns]
    cone = generate_forecast_cone(data)
    print(cone)