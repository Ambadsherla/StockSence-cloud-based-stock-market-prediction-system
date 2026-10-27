import os
import requests
from dotenv import load_dotenv

load_dotenv()

ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN")
if not ACCESS_TOKEN:
    raise SystemExit("No UPSTOX_ACCESS_TOKEN in .env - run realtime/upstox_auth.py first.")

INSTRUMENT_KEYS = {
    "RELIANCE.NS": "NSE_EQ|INE002A01018",
    "TCS.NS": "NSE_EQ|INE467B01029",
    "INFY.NS": "NSE_EQ|INE009A01021",
    "HDFCBANK.NS": "NSE_EQ|INE040A01034",
    "ICICIBANK.NS": "NSE_EQ|INE090A01021",
}

TICKER = "RELIANCE.NS"
HEADERS = {"Accept": "application/json", "Authorization": f"Bearer {ACCESS_TOKEN}"}

# Step 1: discover valid expiries (don't guess the date)
resp = requests.get(
    "https://api.upstox.com/v2/option/contract",
    headers=HEADERS,
    params={"instrument_key": INSTRUMENT_KEYS[TICKER]},
)
print("option/contract status:", resp.status_code)
if resp.status_code != 200:
    print(resp.text)
    raise SystemExit(1)

contracts = resp.json()["data"]
expiries = sorted({c["expiry"] for c in contracts})
print("Valid expiries:", expiries[:5])
nearest = expiries[0]
print("Using nearest expiry:", nearest, "\n")

# Step 2: fetch the option chain for that expiry
resp = requests.get(
    "https://api.upstox.com/v2/option/chain",
    headers=HEADERS,
    params={"instrument_key": INSTRUMENT_KEYS[TICKER], "expiry_date": nearest},
)
print("option/chain status:", resp.status_code)
if resp.status_code != 200:
    print(resp.text)
    raise SystemExit(1)

chain = resp.json()["data"]
print(f"Got {len(chain)} strikes for {TICKER}\n")
if chain:
    print("Spot price:", chain[0].get("underlying_spot_price"), "\n")
    print(f"{'Strike':>10} | {'CE OI':>10} | {'CE LTP':>8} | {'PE OI':>10} | {'PE LTP':>8}")
    for row in chain[:5]:
        ce = row.get("call_options", {}).get("market_data", {})
        pe = row.get("put_options", {}).get("market_data", {})
        print(f"{row['strike_price']:>10} | {ce.get('oi', 0):>10} | {ce.get('ltp', 0):>8} | {pe.get('oi', 0):>10} | {pe.get('ltp', 0):>8}")