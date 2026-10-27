"""Compare Upstox API values with what the Upstox app shows for TCS."""
from upstox_chain import fetch_live_chain

r = fetch_live_chain("TCS.NS")
print("expiry:", r["expiry"], " spot:", r["spot"], " PCR:", r["pcr"], " MaxPain:", r["max_pain"])
print("total call OI:", r["total_call_oi"], " total put OI:", r["total_put_oi"], "\n")

print("Strikes to compare with the app (lakh = OI / 100000):")
print(f"{'Strike':>7} | {'CE OI (lakh)':>12} | {'CE LTP':>8} | {'PE OI (lakh)':>12} | {'PE LTP':>7}")
for row in r["strikes"]:
    if row["strike"] in (2200.0, 2300.0, 2400.0):
        print(f"{row['strike']:>7.0f} | {row['ce_oi']/1e5:>12.2f} | {row['ce_ltp']:>8} | "
              f"{row['pe_oi']/1e5:>12.2f} | {row['pe_ltp']:>7}")


def payout(k):
    return sum(x["ce_oi"] * max(0.0, k - x["strike"]) + x["pe_oi"] * max(0.0, x["strike"] - k)
               for x in r["strikes"])


print("\nFive lowest-payout strikes (the first one is Max Pain):")
for x in sorted(r["strikes"], key=lambda s: payout(s["strike"]))[:5]:
    print(f"  {x['strike']:.0f}   total payout = {payout(x['strike']):,.0f}")