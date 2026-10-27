"""Download 3 years of daily prices and rebuild data/processed/*.csv (same columns as clean_data.py)."""
import os
import pandas as pd
import yfinance as yf

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TICKERS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]


def refresh_ticker(ticker):
    raw = yf.Ticker(ticker).history(period="3y").reset_index()
    df = raw[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].dropna().copy()
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
    df = df.drop_duplicates('Date').sort_values('Date').reset_index(drop=True)
    if len(df) < 150:
        raise ValueError(f"only {len(df)} rows downloaded")
    df['MA_20'] = df['Close'].rolling(20).mean()
    df['MA_50'] = df['Close'].rolling(50).mean()
    df['Daily_Return'] = df['Close'].pct_change() * 100
    df = df.dropna().reset_index(drop=True)

    out_dir = os.path.join(BASE_DIR, 'data', 'processed')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{ticker.replace('.', '_')}_data.csv")
    df.to_csv(path + ".tmp", index=False)
    os.replace(path + ".tmp", path)      # never leaves a half-written CSV
    return len(df)


def refresh_data():
    report = {}
    for t in TICKERS:
        try:
            report[t] = {"rows": refresh_ticker(t)}
        except Exception as e:
            report[t] = {"error": f"{type(e).__name__}: {e}"}
    return report


if __name__ == "__main__":
    print(refresh_data())