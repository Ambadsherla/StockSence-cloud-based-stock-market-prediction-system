"""Test the Gemini layer with DUMMY values (not your real data).  Run: python model/test_gemini_ai.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gemini_ai

context = {
    "current_price": 2500.00,
    "current_price_source": "dummy test value",
    "ma_20": 2480.0,
    "ma_50": 2450.0,
    "ma_crossover_signal": "BUY",
    "rsi_signal": "HOLD",
    "macd_signal": "BUY",
    "linear_regression_next_close": 2512.0,
    "option_chain_source": "SIMULATED",
    "pcr": 1.06,
    "max_pain": 2500.0,
}

print("Model:", gemini_ai.MODEL_NAME, "\nCalling Gemini directly...\n")
try:
    print(gemini_ai._call_gemini("TEST.NS", context))
except Exception as exc:
    print("FAILED:", type(exc).__name__, exc)
    print("\nModels your key can use (look for a 'flash' name):")
    try:
        for m in gemini_ai._get_client().models.list():
            if "flash" in m.name:
                print("  ", m.name)
    except Exception as exc2:
        print("   could not list models:", exc2)
    sys.exit(1)

a = gemini_ai.get_ai_analysis("TEST.NS", context)
b = gemini_ai.get_ai_analysis("TEST.NS", context)
print("\nWrapper source:", a["source"], "| second call came from cache:", a["generated_at"] == b["generated_at"])