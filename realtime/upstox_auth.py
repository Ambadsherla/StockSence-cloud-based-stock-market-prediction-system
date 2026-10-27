"""
Upstox OAuth helper — run this once per day BEFORE starting Flask.

Why this exists: Upstox access tokens expire at 3:30 AM the *next* day,
regardless of when they were generated (documented Upstox behavior, not
a bug here). So each morning / before a demo, re-run this script to get
a fresh UPSTOX_ACCESS_TOKEN written into your .env file.

Usage:
    (venv) $ python realtime/upstox_auth.py
"""

import os
import webbrowser
import requests
from dotenv import load_dotenv, set_key

load_dotenv()

API_KEY = os.getenv("UPSTOX_API_KEY")
API_SECRET = os.getenv("UPSTOX_API_SECRET")
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI")
ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")

if not all([API_KEY, API_SECRET, REDIRECT_URI]):
    raise SystemExit(
        "Missing UPSTOX_API_KEY / UPSTOX_API_SECRET / UPSTOX_REDIRECT_URI in .env.\n"
        "Create an app first at https://account.upstox.com/developer/apps"
    )

login_url = (
    "https://api.upstox.com/v2/login/authorization/dialog"
    f"?response_type=code&client_id={API_KEY}&redirect_uri={REDIRECT_URI}"
)

print("=" * 60)
print("Opening Upstox login in your browser...")
print("If it doesn't open automatically, paste this URL manually:\n")
print(login_url)
print("=" * 60)
webbrowser.open(login_url)

print(
    "\nLog in with your Upstox mobile number / UCC, password, and TOTP.\n"
    "After login, your browser will redirect to a URL starting with\n"
    f"{REDIRECT_URI} and containing '?code=...'.\n"
    "The page itself may say 'can't be reached' or show an error — that's\n"
    "fine, ignore it. Copy the FULL URL from the address bar.\n"
)

redirected_url = input("Paste the full redirected URL here: ").strip()

if "code=" in redirected_url:
    code = redirected_url.split("code=")[1].split("&")[0]
else:
    code = redirected_url  # in case they pasted just the code itself

token_url = "https://api.upstox.com/v2/login/authorization/token"
payload = {
    "code": code,
    "client_id": API_KEY,
    "client_secret": API_SECRET,
    "redirect_uri": REDIRECT_URI,
    "grant_type": "authorization_code",
}
headers = {
    "accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
}

resp = requests.post(token_url, data=payload, headers=headers)

if resp.status_code != 200:
    print(f"\nToken exchange failed ({resp.status_code}):")
    print(resp.text)
    raise SystemExit(1)

data = resp.json()
access_token = data["access_token"]

set_key(ENV_PATH, "UPSTOX_ACCESS_TOKEN", access_token)

print(f"\nSuccess. Logged in as: {data.get('user_name')} ({data.get('email')})")
print("Access token saved to .env as UPSTOX_ACCESS_TOKEN.")
print("Valid until 3:30 AM tomorrow — re-run this script before your next session.")