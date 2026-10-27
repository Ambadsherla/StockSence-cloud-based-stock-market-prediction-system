import pandas as pd
import os

def clean_and_process(ticker):
    """
    Loads raw CSV, cleans it, adds technical indicators,
    and saves a processed version.
    """
    file_name = f"{ticker.replace('.', '_')}_data.csv"
    raw_path = f"data/{file_name}"
    
    df = pd.read_csv(raw_path)
    
    # Keep only needed columns
    df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
    
    # Drop any missing values
    df.dropna(inplace=True)
    
    # Add Moving Averages (used later by ML model)
    df['MA_20'] = df['Close'].rolling(window=20).mean()
    df['MA_50'] = df['Close'].rolling(window=50).mean()
    
    # Add Daily Return (%)
    df['Daily_Return'] = df['Close'].pct_change() * 100
    
    # Drop rows with NaN created by rolling averages
    df.dropna(inplace=True)
    
    # Save processed file
    if not os.path.exists("data/processed"):
        os.makedirs("data/processed")
    
    processed_path = f"data/processed/{file_name}"
    df.to_csv(processed_path, index=False)
    print(f"Processed {ticker} -> {processed_path} ({len(df)} rows)")
    
    return df

if __name__ == "__main__":
    tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
    
    for ticker in tickers:
        clean_and_process(ticker)