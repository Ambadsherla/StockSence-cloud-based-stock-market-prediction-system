import yfinance as yf
import pandas as pd
import os

def fetch_stock_data(ticker, period="1y"):
    """
    Fetches historical stock data for a given ticker symbol.
    ticker: e.g. 'RELIANCE.NS' for NSE stocks
    period: how much historical data to pull (e.g. '1y' = 1 year)
    """
    stock = yf.Ticker(ticker)
    df = stock.history(period=period)
    
    # Create 'data' folder path if not present
    if not os.path.exists("data"):
        os.makedirs("data")
    
    # Save to CSV
    file_path = f"data/{ticker.replace('.', '_')}_data.csv"
    df.to_csv(file_path)
    print(f"Data saved successfully to {file_path}")
    print(f"Total rows fetched: {len(df)}")
    
    return df

if __name__ == "__main__":
    # List of NSE stocks to track
    tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
    
    for ticker in tickers:
        print(f"\nFetching data for {ticker}...")
        fetch_stock_data(ticker)