"""
realtime/upstox_chain.py
LIVE tier of the option-chain fallback (Upstox API v2).
Raises UpstoxChainError on ANY failure so option_chain.py can fall back
to MANUAL, then SIMULATED.
Now also returns previous-day OI, volume, close, bid/ask, delta, IV and lot size,
which option_analytics.py uses for flow analysis and trade ideas.
"""
import os
import time
from datetime import date, datetime

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.upstox.com/v2"

INSTRUMENT_KEYS = {
    "RELIANCE.NS": "NSE_EQ|INE002A01018",
    "TCS.NS": "NSE_EQ|INE467B01029",
    "INFY.NS": "NSE_EQ|INE009A01021",
    "HDFCBANK.NS": "NSE_EQ|INE040A01034",
    "ICICIBANK.NS": "NSE_EQ|INE090A01021",
}


class UpstoxChainError(Exception):
    pass


TOKEN_ORDER = ["UPSTOX_ACCESS_TOKEN", "UPSTOX_ANALYTICS_TOKEN"]   # daily token first, 1-year read-only token as fallback


def _tokens():
    load_dotenv(override=True)  # pick up a fresh token without restarting Flask
    found = [(n, (os.getenv(n) or "").strip()) for n in TOKEN_ORDER]
    found = [(n, t) for n, t in found if t]
    if not found:
        raise UpstoxChainError("No Upstox token in .env - run realtime/upstox_auth.py")
    return found


def _get(path, params):
    rejected = []
    for name, token in _tokens():
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=10)
        except requests.RequestException as exc:
            raise UpstoxChainError(f"Network error: {exc}") from exc
        if resp.status_code == 401:
            rejected.append(name)
            continue                      # expired or not accepted: try the next token
        if resp.status_code != 200:
            raise UpstoxChainError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("data", [])
    raise UpstoxChainError(f"Access token expired or rejected ({', '.join(rejected)}) - run realtime/upstox_auth.py")


def get_nearest_expiry(instrument_key):
    """Returns (expiry 'YYYY-MM-DD', lot_size or None)."""
    contracts = _get("/option/contract", {"instrument_key": instrument_key})
    today = date.today().isoformat()
    expiries = sorted({str(c["expiry"])[:10] for c in contracts if c.get("expiry")})
    upcoming = [e for e in expiries if e >= today]
    if not upcoming:
        raise UpstoxChainError("No upcoming expiry found")
    expiry = upcoming[0]
    lot = next((int(c["lot_size"]) for c in contracts
                if str(c.get("expiry"))[:10] == expiry and c.get("lot_size")), None)
    return expiry, lot


def compute_max_pain(rows):
    """Strike at which total payout to option buyers is smallest."""
    best_strike, best_payout = None, float("inf")
    for cand in rows:
        k = cand["strike"]
        payout = sum(
            r["ce_oi"] * max(0.0, k - r["strike"]) + r["pe_oi"] * max(0.0, r["strike"] - k)
            for r in rows
        )
        if payout < best_payout:
            best_strike, best_payout = k, payout
    return best_strike


def _side(market_data, greeks, p):
    """Upstox sometimes returns empty dicts for strikes with no activity, so default everything."""
    md, gr = market_data or {}, greeks or {}
    f = lambda k: float(md.get(k) or 0)
    return {
        f"{p}_oi": f("oi"), f"{p}_ltp": f("ltp"), f"{p}_prev_oi": f("prev_oi"),
        f"{p}_vol": f("volume"), f"{p}_close": f("close_price"),
        f"{p}_bid": f("bid_price"), f"{p}_ask": f("ask_price"),
        f"{p}_delta": gr.get("delta"), f"{p}_iv": gr.get("iv"),
    }


def fetch_live_chain(ticker):
    key = INSTRUMENT_KEYS.get(ticker)
    if key is None:
        raise UpstoxChainError(f"No instrument key configured for {ticker}")

    expiry, lot_size = get_nearest_expiry(key)
    data = _get("/option/chain", {"instrument_key": key, "expiry_date": expiry})
    if not data:
        raise UpstoxChainError("Empty option chain returned")

    rows = []
    for item in data:
        ce, pe = item.get("call_options") or {}, item.get("put_options") or {}
        row = {"strike": float(item["strike_price"])}
        row.update(_side(ce.get("market_data"), ce.get("option_greeks"), "ce"))
        row.update(_side(pe.get("market_data"), pe.get("option_greeks"), "pe"))
        rows.append(row)
    rows.sort(key=lambda r: r["strike"])

    total_ce = sum(r["ce_oi"] for r in rows)
    total_pe = sum(r["pe_oi"] for r in rows)
    if total_ce == 0 and total_pe == 0:
        raise UpstoxChainError("Chain returned but has no open interest")

    return {
        "ticker": ticker,
        "source": "LIVE",
        "expiry": expiry,
        "lot_size": lot_size,
        "spot": data[0].get("underlying_spot_price"),
        "pcr": round(total_pe / total_ce, 3) if total_ce else None,
        "max_pain": compute_max_pain(rows),
        "total_call_oi": total_ce,
        "total_put_oi": total_pe,
        "strikes": rows,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    import sys
    if "--analytics" in sys.argv:         # test the 1-year read-only token on its own
        TOKEN_ORDER[:] = ["UPSTOX_ANALYTICS_TOKEN"]
    for t in INSTRUMENT_KEYS:
        try:
            r = fetch_live_chain(t)
            print(f"{t:13} expiry={r['expiry']}  lot={r['lot_size']}  spot={r['spot']}  PCR={r['pcr']}  "
                  f"MaxPain={r['max_pain']}  strikes={len(r['strikes'])}")
        except UpstoxChainError as exc:
            print(f"{t:13} FAILED: {exc}")
        time.sleep(0.4)