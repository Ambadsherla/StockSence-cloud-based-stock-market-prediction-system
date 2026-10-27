"""
1) Linear Regression: predicts the NEXT day's close (target is Close shifted by -1).
2) Random Forest classifier: will the close be higher in 5 trading days? Validated with
   walk-forward splits (TimeSeriesSplit, with a gap so no future data leaks into training).
3) Compares MA / RSI / MACD / ML on the SAME out-of-sample period and picks the best by
   return-to-drawdown. Writes model/results/ml_summary.csv for the dashboard.
"""
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import TimeSeriesSplit

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(BASE_DIR, 'strategy_engine'))
sys.path.append(os.path.join(BASE_DIR, 'backtesting'))
from strategies import moving_average_crossover, rsi_strategy, macd_strategy
from backtester import backtest_strategy

TICKERS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
HORIZON = 5
BUY_T, SELL_T = 0.55, 0.45
FEATURES = ['rsi', 'macd_hist', 'ma_ratio', 'ret_1', 'ret_5', 'ret_20', 'vol_chg', 'volatility']
STRATS = {"MA Crossover": moving_average_crossover, "RSI Strategy": rsi_strategy, "MACD Strategy": macd_strategy}
MODEL_DIR = os.path.join(BASE_DIR, 'model', 'saved_models')
RESULT_PATH = os.path.join(BASE_DIR, 'model', 'results', 'ml_summary.csv')


def load(ticker):
    return pd.read_csv(os.path.join(BASE_DIR, 'data', 'processed', f"{ticker.replace('.', '_')}_data.csv"))


def make_features(df):
    c = df['Close']
    d = c.diff()
    rs = d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    f = pd.DataFrame(index=df.index)
    f['rsi'] = 100 - 100 / (1 + rs)
    f['macd_hist'] = (macd - macd.ewm(span=9, adjust=False).mean()) / c
    f['ma_ratio'] = df['MA_20'] / df['MA_50'] - 1
    f['ret_1'] = c.pct_change()
    f['ret_5'] = c.pct_change(5)
    f['ret_20'] = c.pct_change(20)
    f['vol_chg'] = df['Volume'].pct_change(5).clip(-1, 3)
    f['volatility'] = c.pct_change().rolling(10).std()
    return f.replace([np.inf, -np.inf], np.nan)


def make_rf():
    return RandomForestClassifier(n_estimators=200, max_depth=4, min_samples_leaf=10,
                                  random_state=42, n_jobs=-1)


def train_linear(ticker, df):
    """Ridge on stationary features -> next-day % return. (Raw price/volume levels extrapolate badly.)"""
    feats = make_features(df)
    d = feats.assign(target=(df['Close'].shift(-1) / df['Close'] - 1) * 100).dropna()
    X, y = d[FEATURES], d['target']
    cut = int(len(d) * 0.8)

    def mk():
        return make_pipeline(StandardScaler(), Ridge(alpha=50.0))

    pred = mk().fit(X.iloc[:cut], y.iloc[:cut]).predict(X.iloc[cut:])
    yt = y.iloc[cut:]
    rmse = float(np.sqrt(mean_squared_error(yt, pred)))
    zero = float(np.sqrt(mean_squared_error(yt, np.zeros(len(yt)))))
    diracc = float((np.sign(pred) == np.sign(yt)).mean() * 100)
    print(f"{ticker:14s} next-day return model: RMSE {rmse:.3f}% (always-0 baseline {zero:.3f}%), direction {diracc:.1f}%")
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(mk().fit(X, y), os.path.join(MODEL_DIR, f"{ticker.replace('.', '_')}_model.pkl"))


def _score(m):
    if m['total_trades'] < 3:
        return -1e9
    return m['total_return_pct'] / max(abs(m['max_drawdown_pct']), 1.0)


def _bt(sdf):
    return backtest_strategy(sdf.reset_index(drop=True))


def train_classifier(ticker, df):
    feats = make_features(df)
    data = feats.assign(fwd=df['Close'].shift(-HORIZON) / df['Close'] - 1).dropna()
    X, y = data[FEATURES], (data['fwd'] > 0).astype(int)

    oof = pd.Series(np.nan, index=data.index)
    for tr, te in TimeSeriesSplit(n_splits=5, gap=HORIZON).split(X):
        m = make_rf().fit(X.iloc[tr], y.iloc[tr])
        oof.iloc[te] = m.predict_proba(X.iloc[te])[:, 1]
    ok = oof.notna()
    p, yt, fw = oof[ok], y[ok], data.loc[ok, 'fwd']

    accuracy = float(((p > 0.5) == (yt == 1)).mean() * 100)
    baseline = float(max(yt.mean(), 1 - yt.mean()) * 100)
    buys = p > BUY_T
    signal_win = float((fw[buys] > 0).mean() * 100) if buys.sum() >= 10 else None

    start = p.index[0]
    comp = {name: _bt(fn(df).loc[start:]) for name, fn in STRATS.items()}
    mdf = df.loc[p.index].copy()
    mdf['Signal'] = np.where(p > BUY_T, 'BUY', np.where(p < SELL_T, 'SELL', 'HOLD'))
    comp['ML Classifier'] = _bt(mdf)

    best = max(comp, key=lambda k: _score(comp[k]))
    enough = _score(comp[best]) > -1e8 and comp[best]['total_return_pct'] > 0   # a losing strategy is not 'best'
    has_edge = accuracy > baseline

    final = make_rf().fit(X, y)
    joblib.dump(final, os.path.join(MODEL_DIR, f"{ticker.replace('.', '_')}_clf.pkl"))
    last = feats.iloc[[-1]][FEATURES]
    prob = None if last.isna().any().any() else float(final.predict_proba(last)[0, 1])
    ml_signal = None if prob is None else ('BUY' if prob > BUY_T else 'SELL' if prob < SELL_T else 'HOLD')
    if not has_edge and ml_signal:
        ml_signal = 'HOLD'          # no edge over guessing -> do not issue directional signals

    return {
        "Ticker": ticker,
        "best_strategy": best if enough else "None (no profitable strategy)",
        "best_win_rate_oos": comp[best]['win_rate'] if enough else None,
        "best_return_pct_oos": comp[best]['total_return_pct'] if enough else None,
        "best_max_drawdown_pct_oos": comp[best]['max_drawdown_pct'] if enough else None,
        "best_trades_oos": comp[best]['total_trades'] if enough else None,
        "ml_has_edge": int(has_edge),
        "ml_accuracy_oos": round(accuracy, 1),
        "baseline_accuracy": round(baseline, 1),
        "ml_buy_signal_win_pct": None if signal_win is None else round(signal_win, 1),
        "ml_buy_signals": int(buys.sum()),
        "ml_prob_up": None if prob is None else round(prob, 3),
        "ml_signal": ml_signal,
        "oos_days": int(ok.sum()),
        "oos_start": str(df.loc[start, 'Date'])[:10],
        "comparison": json.dumps([{"name": k, **{a: float(b) for a, b in v.items()}} for k, v in comp.items()]),
    }


def train_all():
    rows = []
    for t in TICKERS:
        try:
            df = load(t)
            train_linear(t, df)
            rows.append(train_classifier(t, df))
        except Exception as e:
            print(f"{t}: training failed - {type(e).__name__}: {e}")
    if rows:
        os.makedirs(os.path.dirname(RESULT_PATH), exist_ok=True)
        pd.DataFrame(rows).to_csv(RESULT_PATH, index=False)
        print("Saved", RESULT_PATH)


if __name__ == "__main__":
    train_all()