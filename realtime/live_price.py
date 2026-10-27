import yfinance as yf
from datetime import datetime

def get_live_price(ticker):
    """
    Fetches the latest available price for a stock.
    Labels the source accurately based on whether data is from today's session.
    """
    stock = yf.Ticker(ticker)
    today = datetime.now().date()
    
    try:
        intraday = stock.history(period="1d", interval="1m")
        if not intraday.empty:
            latest = intraday.iloc[-1]
            latest_date = latest.name.date()
            
            source = "intraday (near real-time)" if latest_date == today else "last session (market closed)"
            
            return {
                "ticker": ticker,
                "price": round(float(latest['Close']), 2),
                "time": str(latest.name),
                "source": source
            }
    except Exception:
        pass
    
    daily = stock.history(period="5d")
    latest = daily.iloc[-1]
    return {
        "ticker": ticker,
        "price": round(float(latest['Close']), 2),
        "time": str(latest.name),
        "source": "last close (market closed)"
    }


def get_live_prices(tickers):
    return [get_live_price(t) for t in tickers]


if __name__ == "__main__":
    tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
    
    print("=" * 60)
    print(f"LIVE PRICE CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    prices = get_live_prices(tickers)
    for p in prices:
        print(f"{p['ticker']:15s} ₹{p['price']:10.2f}  [{p['source']}]  @ {p['time']}")