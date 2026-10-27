"""
realtime/option_analytics.py
Turns a LIVE option chain into: support/resistance walls, a buyer-vs-writer flow estimate,
a market-bias score, and (only when the signal is strong enough) one option trade idea.

Honest limits:
 * Open interest counts OPEN CONTRACTS. Every contract has a buyer AND a seller, so OI alone
   cannot say "N people bought calls". We estimate who is driving new OI by combining the
   change in OI with the change in the option's price (the standard buildup heuristic).
 * Upstox gives only a snapshot, no history, so this bias cannot be backtested yet.
   log_snapshot() saves one row per day so it can be validated later.
"""
import csv
import os
from datetime import date, datetime

SNAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
SNAP_COLS = ["date", "spot", "pcr", "max_pain", "support", "resistance", "bias", "score"]
W = {"pcr": 0.25, "walls": 0.25, "max_pain": 0.20, "flow": 0.30}


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def _side_flow(rows, p):
    """New OI split by what the option price did: price up = buyers, price down = writers."""
    added = removed = buyer = writer = 0.0
    for r in rows:
        chg = r[f"{p}_oi"] - r[f"{p}_prev_oi"]
        ltp, close = r[f"{p}_ltp"], r[f"{p}_close"]
        price_chg = (ltp - close) if ltp > 0 and close > 0 else 0.0
        if chg > 0:
            added += chg
            if price_chg > 0:
                buyer += chg
            elif price_chg < 0:
                writer += chg
        elif chg < 0:
            removed += -chg
    return {"added": added, "removed": removed, "buyer": buyer, "writer": writer}


def _strike_step(rows):
    ks = sorted({r["strike"] for r in rows})
    gaps = [b - a for a, b in zip(ks, ks[1:]) if b > a]
    return min(gaps) if gaps else 1.0


def _idea(oc, bias, rows, spot, step, dte, sup, res, strength):
    if bias == "NEUTRAL":
        return None, "Signals are mixed, so no option trade is suggested. Waiting is a valid position."
    if strength < 35:
        return None, "The signal is too weak to justify buying an option."
    if dte is not None and dte < 3:
        return None, f"Expiry is only {dte} day(s) away; time decay makes buying options very risky."

    p = "ce" if bias == "BULLISH" else "pe"
    atm = min(rows, key=lambda r: abs(r["strike"] - spot))["strike"]
    if p == "ce":
        pool = [r for r in rows if atm <= r["strike"] <= atm + 2 * step]
    else:
        pool = [r for r in rows if atm - 2 * step <= r["strike"] <= atm]
    near_oi = sorted(r[f"{p}_oi"] for r in rows if abs(r["strike"] - spot) <= 6 * step)
    med = near_oi[len(near_oi) // 2] if near_oi else 0
    pool = [r for r in pool if r[f"{p}_ltp"] > 0 and r[f"{p}_oi"] >= 0.5 * med]
    if not pool:
        return None, "No liquid contract near the money (no recent trades or too little open interest)."

    def closeness(r):
        d = r.get(f"{p}_delta")
        return abs(abs(d) - 0.45) if d else abs(r["strike"] - atm) / step * 0.1

    r = min(pool, key=closeness)
    prem, k = r[f"{p}_ltp"], r["strike"]
    be = k + prem if p == "ce" else k - prem
    bid, ask = r[f"{p}_bid"], r[f"{p}_ask"]
    spread = round((ask - bid) / prem * 100, 1) if bid > 0 and ask > 0 else None
    lot = oc.get("lot_size")
    warnings = []
    if spread is not None and spread > 8:
        warnings.append(f"Wide bid-ask spread ({spread}%): you lose a lot just entering and exiting.")
    if dte is not None and dte <= 7:
        warnings.append(f"Only {dte} days to expiry: time decay works against buyers.")
    iv = r.get(f"{p}_iv")
    if iv and iv > 60:
        warnings.append(f"Implied volatility is high ({iv:.0f}%): premiums are expensive.")
    return {
        "type": "CALL" if p == "ce" else "PUT",
        "label": f"{oc['ticker'].replace('.NS', '')} {k:g} {p.upper()}",
        "strike": k, "expiry": oc.get("expiry"), "dte": dte,
        "premium": round(prem, 2), "lot_size": lot,
        "cost_per_lot": round(prem * lot, 2) if lot else None,
        "breakeven": round(be, 2), "breakeven_move_pct": round((be - spot) / spot * 100, 2),
        "delta": r.get(f"{p}_delta"), "iv": iv, "spread_pct": spread, "oi": r[f"{p}_oi"],
        "invalid_if": (f"the stock closes below support {sup['strike']:g}" if p == "ce"
                       else f"the stock closes above resistance {res['strike']:g}")
                      if sup and res else "the price moves against the trade",
        "warnings": warnings,
    }, None


def analyze_chain(oc):
    rows = oc.get("strikes") or []
    spot = oc.get("spot")
    if oc.get("source") != "LIVE" or not rows or not spot:
        return {"available": False, "note": "needs a live chain"}
    spot = float(spot)
    step = _strike_step(rows)
    near = sorted(rows, key=lambda r: abs(r["strike"] - spot))[:13]

    below = [r for r in rows if r["strike"] <= spot] or rows
    above = [r for r in rows if r["strike"] >= spot] or rows
    s_row = max(below, key=lambda r: r["pe_oi"])
    r_row = max(above, key=lambda r: r["ce_oi"])
    sup = {"strike": s_row["strike"], "oi": s_row["pe_oi"]} if s_row["pe_oi"] > 0 else None
    res = {"strike": r_row["strike"], "oi": r_row["ce_oi"]} if r_row["ce_oi"] > 0 else None

    ce_vol, pe_vol = sum(r["ce_vol"] for r in rows), sum(r["pe_vol"] for r in rows)
    pcr_vol = round(pe_vol / ce_vol, 3) if ce_vol else None

    dte = None
    try:
        dte = (date.fromisoformat(str(oc["expiry"])[:10]) - date.today()).days
    except Exception:
        pass

    flow_ok = sum(r["ce_prev_oi"] + r["pe_prev_oi"] for r in near) > 0
    ce, pe = _side_flow(near, "ce"), _side_flow(near, "pe")
    bull, bear = ce["buyer"] + pe["writer"], pe["buyer"] + ce["writer"]

    comps, reasons = {}, []
    pcr = oc.get("pcr")
    if pcr:
        comps["pcr"] = 1.0 if pcr > 1.3 else 0.5 if pcr > 1.1 else 0.0 if pcr >= 0.9 else -0.5 if pcr >= 0.7 else -1.0
        reasons.append(f"PCR is {pcr:.2f}: " + ("more put than call open interest (supportive)" if comps["pcr"] > 0
                       else "more call than put open interest (cautious)" if comps["pcr"] < 0 else "balanced"))
    if sup and res and res["strike"] > sup["strike"]:
        pos = (spot - sup["strike"]) / (res["strike"] - sup["strike"])
        comps["walls"] = _clip(1 - 2 * pos)
        reasons.append(f"Price sits {'near support' if pos < 0.4 else 'near resistance' if pos > 0.6 else 'mid-range'} "
                       f"between support {sup['strike']:g} and resistance {res['strike']:g}")
    mp = oc.get("max_pain")
    mp_diff = None
    if mp:
        mp_diff = round((mp - spot) / spot * 100, 2)
        comps["max_pain"] = _clip(mp_diff / 2) * (1.0 if dte is not None and dte <= 7 else 0.5)
        reasons.append(f"Max pain {mp:g} is {mp_diff:+.1f}% from spot (pull strengthens near expiry)")
    if flow_ok and bull + bear > 0:
        comps["flow"] = (bull - bear) / (bull + bear)
        reasons.append("New positions lean " + ("bullish (call buying / put writing)" if comps["flow"] > 0.1
                       else "bearish (put buying / call writing)" if comps["flow"] < -0.1 else "evenly split"))
    elif not flow_ok:
        reasons.append("No previous-day OI available, so buyer/writer flow was skipped")
    else:
        reasons.append("Open interest fell at every nearby strike (positions are being closed), so buyer/writer flow adds nothing new")

    wsum = sum(W[k] for k in comps)
    score = sum(comps[k] * W[k] for k in comps) / wsum if wsum else 0.0
    bias = "BULLISH" if score > 0.25 else "BEARISH" if score < -0.25 else "NEUTRAL"
    strength = int(round(min(100, abs(score) * 150)))
    if dte is not None and dte <= 1:
        strength = int(strength * 0.6)
        reasons.append("Expiry is today or tomorrow: PCR and max pain are distorted by expiring contracts, "
                       "so the strength is reduced and this bias is low-reliability")
    idea, idea_note = _idea(oc, bias, rows, spot, step, dte, sup, res, strength)

    return {
        "available": True, "bias": bias, "score": round(score, 2), "strength": strength,
        "reasons": reasons, "support": sup, "resistance": res, "pcr_volume": pcr_vol,
        "max_pain_diff_pct": mp_diff, "dte": dte, "flow_available": flow_ok,
        "flow": {"ce": ce, "pe": pe}, "idea": idea, "idea_note": idea_note,
    }


def log_snapshot(oc, an):
    """One row per day per stock (the latest fetch of the day wins) for later validation."""
    if not an or not an.get("available"):
        return
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, oc["ticker"].replace(".", "_") + ".csv")
    today = date.today().isoformat()
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = [r for r in csv.reader(f) if r and r[0] not in (today, "date")]
    rows.append([today, oc.get("spot"), oc.get("pcr"), oc.get("max_pain"),
                 (an.get("support") or {}).get("strike"), (an.get("resistance") or {}).get("strike"),
                 an["bias"], an["score"]])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(SNAP_COLS)
        w.writerows(rows)