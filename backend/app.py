from flask import Flask, jsonify, send_from_directory, request, abort, Response
import pandas as pd
import sys, os, time, threading, joblib
from datetime import datetime
from zoneinfo import ZoneInfo

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
for sub in ('strategy_engine', 'realtime', 'model', 'data_ingestion', 'backtesting'):
    sys.path.append(os.path.join(BASE_DIR, sub))

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, '.env'))

from strategies import moving_average_crossover, rsi_strategy, macd_strategy
from live_price import get_live_price, get_live_prices
from option_chain import get_option_summary
from option_analytics import analyze_chain, log_snapshot
from gemini_ai import get_ai_analysis
from refresh_data import refresh_data
from run_pipeline import full_update
from auth import init_auth
from train_model import make_features, FEATURES
import json

app = Flask(__name__)
init_auth(app, BASE_DIR)      # login / logout / session gate (backend/auth.py)

TICKERS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')
IST = ZoneInfo("Asia/Kolkata")
_refresh_lock = threading.Lock()


def p(*parts):
    return os.path.join(BASE_DIR, *parts)


def check(ticker):
    if ticker not in TICKERS:
        abort(404)


def load_processed(ticker):
    check(ticker)
    return pd.read_csv(p('data', 'processed', f"{ticker.replace('.', '_')}_data.csv"))


# ---------- background refresh ----------
def safe_refresh():
    if not _refresh_lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        return refresh_data()
    finally:
        _refresh_lock.release()


def safe_full_update():
    """refresh + retrain + backtest (slow: run after close or when data is stale)"""
    if not _refresh_lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        return full_update()
    finally:
        _refresh_lock.release()


def market_open(now):
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 30)


def scheduler():
    last_close_run = None
    try:                                    # catch up on startup if the CSVs are old
        age = (datetime.now() - pd.to_datetime(load_processed(TICKERS[0]).iloc[-1]['Date'][:10])).days
        if age > 4:
            safe_full_update()
    except Exception as e:
        print("startup update error:", e)
    while True:
        now = datetime.now(IST)
        try:
            if market_open(now):
                safe_refresh()
                time.sleep(900)             # every 15 min while open
                continue
            if now.weekday() < 5 and now.hour >= 16 and last_close_run != now.date():
                safe_full_update()          # one full update after close
                last_close_run = now.date()
        except Exception as e:
            print("scheduler error:", e)
        time.sleep(300)


@app.route('/')
def dashboard():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/api/status')
def status():
    df = load_processed(TICKERS[0])
    return jsonify({"data_as_of": str(df.iloc[-1]['Date'])[:10]})


@app.route('/api/refresh', methods=['POST'])
def refresh():
    return jsonify(safe_refresh())


@app.route('/api/watchlist')
def watchlist():
    return jsonify(get_live_prices(TICKERS))


@app.route('/api/price/<ticker>')
def price(ticker):
    check(ticker)
    return jsonify(get_live_price(ticker))


@app.route('/api/history/<ticker>')
def history(ticker):
    df = load_processed(ticker)
    return Response(df.to_json(orient='records'), mimetype='application/json')  # NaN -> null


@app.route('/api/strategies/<ticker>')
def strategies(ticker):
    df = load_processed(ticker)
    results = {
        "MA Crossover": moving_average_crossover(df.copy()),
        "RSI Strategy": rsi_strategy(df.copy()),
        "MACD Strategy": macd_strategy(df.copy()),
    }
    response = {}
    for name, sdf in results.items():
        sigs = sdf[sdf['Signal'] != 'HOLD'][['Date', 'Close', 'Signal']].to_dict(orient='records')
        response[name] = {"latest_signal": sdf.iloc[-1]['Signal'], "signal_history": sigs}
    return jsonify(response)


@app.route('/api/backtest')
def backtest_all():
    df = pd.read_csv(p('backtesting', 'results', 'backtest_summary.csv'))
    return jsonify(df.to_dict(orient='records'))


@app.route('/api/backtest/<ticker>')
def backtest_ticker(ticker):
    check(ticker)
    df = pd.read_csv(p('backtesting', 'results', 'backtest_summary.csv'))
    return jsonify(df[df['Ticker'] == ticker].to_dict(orient='records'))


@app.route('/api/predict/<ticker>')
def predict(ticker):
    check(ticker)
    model = joblib.load(p('model', 'saved_models', f"{ticker.replace('.', '_')}_model.pkl"))
    df = load_processed(ticker)
    latest = df.iloc[-1]
    feats = make_features(df).iloc[[-1]][FEATURES]
    ret = 0.0 if feats.isna().any().any() else float(model.predict(feats)[0])
    ret = max(-5.0, min(5.0, ret))                       # sanity clip on a one-day move
    current = float(latest['Close'])
    predicted = current * (1 + ret / 100)
    return jsonify({
        "ticker": ticker,
        "current_price": round(current, 2),
        "predicted_next_close": round(predicted, 2),
        "change_pct": round(ret, 2),
    })


def with_analysis(oc):
    """Attach option-flow analysis to a LIVE chain (other tiers have no real flow data)."""
    if oc.get("source") != "LIVE":
        return oc
    try:
        an = analyze_chain(oc)
        log_snapshot(oc, an)
    except Exception as exc:
        an = {"available": False, "note": f"{type(exc).__name__}: {exc}"}
    return {**oc, "analysis": an}


def read_ml(ticker):
    path = p('model', 'results', 'ml_summary.csv')
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    row = df[df['Ticker'] == ticker]
    if row.empty:
        return None
    r = row.iloc[0].astype(object).where(row.iloc[0].notna(), None).to_dict()
    r['comparison'] = json.loads(r['comparison']) if r.get('comparison') else []
    return r


@app.route('/api/ml-summary/<ticker>')
def ml_summary(ticker):
    check(ticker)
    r = read_ml(ticker)
    if r is None:
        return jsonify({"error": "Run python data_ingestion/run_pipeline.py first"}), 404
    return jsonify(r)


@app.route('/api/optionchain/<ticker>')
def optionchain(ticker):
    check(ticker)
    return jsonify(with_analysis(get_option_summary(ticker, force=request.args.get('force'))))


def _find_price(info):
    if isinstance(info, dict):
        for key in ("price", "live_price", "current_price", "last_price", "ltp", "close", "Close"):
            v = info.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
    return None


def build_ai_context(ticker):
    df = load_processed(ticker)
    latest = df.iloc[-1]

    live = None
    try:
        live = _find_price(get_live_price(ticker))
    except Exception:
        pass
    price_now = live if live is not None else float(latest['Close'])

    # ATR(14), support/resistance (20-day low/high) computed here, not by the LLM
    prev_close = df['Close'].shift(1)
    tr = pd.concat([df['High'] - df['Low'], (df['High'] - prev_close).abs(),
                    (df['Low'] - prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])

    ctx = {
        "current_price": round(price_now, 2),
        "current_price_source": "live price" if live is not None else "last processed close (may be stale)",
        "last_processed_date": str(latest['Date'])[:10],
        "ma_20": round(float(latest['MA_20']), 2),
        "ma_50": round(float(latest['MA_50']), 2),
        "atr": round(atr, 2),
        "support": round(float(df['Low'].tail(20).min()), 2),
        "resistance": round(float(df['High'].tail(20).max()), 2),
        "ma_crossover_signal": moving_average_crossover(df.copy()).iloc[-1]['Signal'],
        "rsi_signal": rsi_strategy(df.copy()).iloc[-1]['Signal'],
        "macd_signal": macd_strategy(df.copy()).iloc[-1]['Signal'],
    }
    try:
        model = joblib.load(p('model', 'saved_models', f"{ticker.replace('.', '_')}_model.pkl"))
        _f = make_features(df).iloc[[-1]][FEATURES]
        ctx["model_next_day_return_pct"] = round(float(model.predict(_f)[0]), 2)
    except Exception:
        pass
    try:
        bt = pd.read_csv(p('backtesting', 'results', 'backtest_summary.csv'))
        ctx["backtest_results"] = bt[bt['Ticker'] == ticker].to_dict(orient='records')
    except Exception:
        pass
    try:
        ml = read_ml(ticker)
        if ml:
            ctx.update({"ml_best_strategy": ml["best_strategy"],
                        "ml_expected_win_ratio_pct_out_of_sample": ml["best_win_rate_oos"],
                        "ml_5day_direction_accuracy_pct": ml["ml_accuracy_oos"],
                        "ml_baseline_accuracy_pct": ml["baseline_accuracy"],
                        "ml_probability_up_5d": ml["ml_prob_up"], "ml_signal": ml["ml_signal"]})
    except Exception:
        pass
    try:
        oc = get_option_summary(ticker)
        oc = with_analysis(oc)
        an = oc.get("analysis") or {}
        ctx.update({"option_chain_source": oc["source"], "pcr": oc.get("pcr"), "max_pain": oc.get("max_pain"),
                    "option_flow_bias": an.get("bias"), "option_flow_strength_0_100": an.get("strength"),
                    "option_support_strike": (an.get("support") or {}).get("strike"),
                    "option_resistance_strike": (an.get("resistance") or {}).get("strike"),
                    "option_days_to_expiry": an.get("dte")})
    except Exception:
        pass
    return ctx


@app.route('/api/ai-insight/<ticker>')
def ai_insight(ticker):
    check(ticker)
    try:
        context = build_ai_context(ticker)
    except Exception as exc:
        return jsonify({"source": "UNAVAILABLE", "error": f"{type(exc).__name__}: {exc}"})
    return jsonify(get_ai_analysis(ticker, context, force_refresh=request.args.get('refresh') == '1'))


_started = False


def start_background():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=scheduler, daemon=True).start()


if os.getenv("START_SCHEDULER") == "1":      # set on the server: gunicorn imports this module
    start_background()

if __name__ == "__main__":
    start_background()
    app.run(host=os.getenv("HOST", "127.0.0.1"), debug=False, port=5000)