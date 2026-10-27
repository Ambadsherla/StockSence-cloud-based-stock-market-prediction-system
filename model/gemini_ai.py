"""
model/gemini_ai.py
AI reasoning layer (Google Gemini). It EXPLAINS numbers the system already computed.
Python computes entry / stop / target levels (ATR-based); Gemini only picks the stance and explains.
Never raises: any failure returns source="UNAVAILABLE" (or the last good answer, flagged stale_cache).
"""
import json
import os
import re
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite").split(",") if m.strip()]
CACHE_TTL_MINUTES = 15
FAILURE_TTL_SECONDS = 60
BASIS = "LLM reasoning over already-computed signals; not a statistical forecast"

_cache = {}
_last_good = {}
_client = None

SYSTEM_INSTRUCTION = """You are an analysis assistant inside a stock analytics dashboard for an
academic project. You receive values the system has ALREADY computed for one NSE stock: strategy
signals, backtest results, an ML classifier's out-of-sample results, a Linear Regression
prediction, and option-chain figures. Explain them in plain English. Use ONLY the supplied
values; never invent numbers, news or events. Do NOT output entry/stop/target prices: the system
computes those itself.

Rules:
- If option_chain_source is SIMULATED, PCR and Max Pain are synthetic: say so and do not base the
  sentiment on them. If MANUAL, they were typed in by the user. If LIVE, they come from the broker API.
- If current_price_source or last_processed_date suggests stale data, say the analysis may lag the market.
- ml_* fields are historical out-of-sample estimates, not guarantees. Say so when you cite them.
- option_flow_* fields come from live open-interest analysis. They are a heuristic (open interest counts open
  contracts, not who is buying). Use them only as supporting evidence, never as a guarantee.
- ai_price_estimate is an illustrative next-trading-day figure near current_price (normally within
  +/-3%). It is weaker than the Linear Regression prediction; never claim otherwise.
- sentiment is BULLISH, BEARISH or NEUTRAL. confidence is an integer 0-100, low when signals disagree.
- horizon is a short phrase such as "5-10 trading days".
- insight is 2-3 plain sentences, no markdown, and is not personal financial advice.

Reply with ONLY a JSON object with exactly these keys: sentiment, confidence, ai_price_estimate, horizon, insight."""


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is missing from .env")
        from google import genai
        _client = genai.Client(api_key=key)
    return _client


def _parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.MULTILINE).strip()
    data = json.loads(text)
    if isinstance(data, list):
        data = data[0]
    sentiment = str(data["sentiment"]).upper()
    if sentiment not in ("BULLISH", "BEARISH", "NEUTRAL"):
        raise ValueError(f"Unexpected sentiment value: {sentiment}")
    return {
        "sentiment": sentiment,
        "confidence": max(0, min(100, int(data["confidence"]))),
        "ai_price_estimate": round(float(data["ai_price_estimate"]), 2),
        "horizon": str(data.get("horizon", "")).strip(),
        "insight": str(data["insight"]).strip(),
    }


def _call_gemini(ticker, context):
    from google.genai import types
    lines = [f"Ticker: {ticker}", "Computed values:"] + [f"- {k}: {v}" for k, v in context.items()]
    config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION,
                                         response_mime_type="application/json", temperature=0.3)
    errors = []
    for model in dict.fromkeys([MODEL_NAME, *FALLBACK_MODELS]):    # primary, then fallbacks
        for attempt in range(2):
            try:
                resp = _get_client().models.generate_content(model=model, contents="\n".join(lines), config=config)
                return {**_parse(resp.text), "model": model}
            except RuntimeError:
                raise                                              # missing key: retrying is pointless
            except Exception as exc:
                errors.append(f"{model}: {str(exc)[:110]}")
                if "404" in str(exc) or "NOT_FOUND" in str(exc):
                    break                                          # retired model: skip to the next one
                time.sleep(2)
    raise RuntimeError(" | ".join(errors))


def _levels(sentiment, ctx):
    price, atr = ctx.get("current_price"), ctx.get("atr")
    if sentiment == "NEUTRAL" or not price or not atr:
        return {}
    s = 1 if sentiment == "BULLISH" else -1
    a, b = price - s * 0.5 * atr, price
    return {
        "side": "LONG" if s == 1 else "EXIT / AVOID",
        "entry_zone": [round(min(a, b), 2), round(max(a, b), 2)],
        "stop_loss": round(price - s * 1.5 * atr, 2),
        "target_1": round(price + s * 1.5 * atr, 2),
        "target_2": round(price + s * 3.0 * atr, 2),
    }


def get_ai_analysis(ticker, context, force_refresh=False):
    now = time.time()
    hit = _cache.get(ticker)
    if not force_refresh and hit and now - hit["at"] < hit["ttl"]:
        return hit["data"]

    try:
        parsed = _call_gemini(ticker, context)
        result = {"source": "GEMINI_AI", **parsed, **_levels(parsed["sentiment"], context),
                  "support": context.get("support"), "resistance": context.get("resistance"),
                  "current_price": context.get("current_price"),
                  "signals": {"MA crossover": context.get("ma_crossover_signal"), "RSI": context.get("rsi_signal"),
                              "MACD": context.get("macd_signal"), "ML model": context.get("ml_signal"),
                              "Options flow": context.get("option_flow_bias")}}
        ttl = CACHE_TTL_MINUTES * 60
        result["basis"] = BASIS
        result["generated_at"] = datetime.now().isoformat(timespec="seconds")
        _last_good[ticker] = result
    except Exception as exc:
        if ticker in _last_good:
            result = {**_last_good[ticker], "stale_cache": True}
        else:
            result = {"source": "UNAVAILABLE", "model": MODEL_NAME, "sentiment": None, "confidence": None,
                      "ai_price_estimate": None, "insight": "AI insight temporarily unavailable.",
                      "error": f"{type(exc).__name__}: {str(exc)[:450]}", "basis": BASIS,
                      "generated_at": datetime.now().isoformat(timespec="seconds")}
        ttl = FAILURE_TTL_SECONDS

    _cache[ticker] = {"data": result, "at": now, "ttl": ttl}
    return result