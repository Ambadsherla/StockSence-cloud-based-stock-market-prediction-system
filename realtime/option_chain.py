"""
realtime/option_chain.py
Three-tier option-chain resolver:
  1. LIVE      - Upstox API (realtime/upstox_chain.py)
  2. MANUAL    - PCR / Max Pain typed in by the user for today (realtime/manual_entry.py)
  3. SIMULATED - synthetic chain anchored to a real spot price, always labelled SIMULATED.
                 It approximates the SHAPE of open interest only, never real positions.
"""
import json
import math
import os
import random
import time
from datetime import date, datetime

import pandas as pd

from upstox_chain import fetch_live_chain, compute_max_pain, INSTRUMENT_KEYS

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MANUAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual_entries")
CACHE_TTL_SECONDS = 60
_cache = {}


def _slug(ticker):
    return ticker.replace(".", "_")


# ---------------- spot price (used by the SIMULATED tier) ----------------
def _last_close(ticker):
    path = os.path.join(PROJECT_ROOT, "data", "processed", f"{_slug(ticker)}_data.csv")
    return float(pd.read_csv(path).iloc[-1]["Close"])


def _spot_price(ticker):
    try:
        import yfinance as yf
        price = yf.Ticker(ticker).fast_info["last_price"]
        if price and price > 0:
            return float(price), "yfinance"
    except Exception:
        pass
    return _last_close(ticker), "last processed close (may be stale)"


# ---------------- MANUAL tier ----------------
def _manual_path(ticker):
    return os.path.join(MANUAL_DIR, f"{_slug(ticker)}_{date.today().isoformat()}.json")


def save_manual_entry(ticker, pcr, max_pain, spot=None, expiry=None):
    os.makedirs(MANUAL_DIR, exist_ok=True)
    record = {
        "ticker": ticker,
        "pcr": float(pcr),
        "max_pain": float(max_pain),
        "spot": float(spot) if spot not in (None, "") else None,
        "expiry": expiry or None,
        "entered_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(_manual_path(ticker), "w") as f:
        json.dump(record, f, indent=2)
    return record


def try_manual(ticker):
    path = _manual_path(ticker)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        rec = json.load(f)
    return {
        "ticker": ticker,
        "source": "MANUAL",
        "expiry": rec.get("expiry"),
        "spot": rec.get("spot"),
        "pcr": rec["pcr"],
        "max_pain": rec["max_pain"],
        "total_call_oi": None,
        "total_put_oi": None,
        "strikes": [],
        "fetched_at": rec.get("entered_at"),
    }


# ---------------- SIMULATED tier ----------------
def _strike_step(spot):
    if spot < 500:
        return 5
    if spot < 1500:
        return 10
    if spot < 3000:
        return 20
    return 50


def generate_simulated_chain(ticker):
    spot, spot_source = _spot_price(ticker)
    step = _strike_step(spot)
    atm = round(spot / step) * step
    rng = random.Random(f"{ticker}-{date.today().isoformat()}")  # stable within a day, no flicker
    rows = []
    for i in range(-12, 13):
        k = float(atm + i * step)
        ce_oi = 100000 * math.exp(-(((k - (spot + 3 * step)) / (5 * step)) ** 2)) * rng.uniform(0.7, 1.3)
        pe_oi = 100000 * math.exp(-(((k - (spot - 3 * step)) / (5 * step)) ** 2)) * rng.uniform(0.7, 1.3)
        time_value = spot * 0.01 * math.exp(-(((k - spot) / (4 * step)) ** 2))
        rows.append({
            "strike": k,
            "ce_oi": float(round(ce_oi, -2)),
            "ce_ltp": round(max(spot - k, 0) + time_value, 2),
            "pe_oi": float(round(pe_oi, -2)),
            "pe_ltp": round(max(k - spot, 0) + time_value, 2),
        })
    total_ce = sum(r["ce_oi"] for r in rows)
    total_pe = sum(r["pe_oi"] for r in rows)
    return {
        "ticker": ticker,
        "source": "SIMULATED",
        "expiry": None,
        "spot": round(spot, 2),
        "spot_source": spot_source,
        "pcr": round(total_pe / total_ce, 3) if total_ce else None,
        "max_pain": compute_max_pain(rows),
        "total_call_oi": total_ce,
        "total_put_oi": total_pe,
        "strikes": rows,
        "note": "Synthetic data anchored to spot price; not real trader positions.",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------- main entry point ----------------
def get_option_summary(ticker, force=None):
    """
    Normal order: LIVE -> MANUAL -> SIMULATED.
    force='manual'    skips LIVE      (for testing / demos)
    force='simulated' skips LIVE and MANUAL
    """
    cache_key = (ticker, force)
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    result, live_error = None, None

    if force is None:
        try:
            result = fetch_live_chain(ticker)
        except Exception as exc:  # any failure -> fall back, never crash the dashboard
            live_error = str(exc)

    if result is None and force in (None, "manual"):
        result = try_manual(ticker)

    if result is None:
        result = generate_simulated_chain(ticker)

    if live_error and result["source"] != "LIVE":
        result["live_error"] = live_error

    _cache[cache_key] = (time.time(), result)
    return result


if __name__ == "__main__":
    for t in INSTRUMENT_KEYS:
        r = get_option_summary(t)
        print(f"{t:13} source={r['source']:9} spot={r['spot']}  PCR={r['pcr']}  "
              f"MaxPain={r['max_pain']}  strikes={len(r['strikes'])}")