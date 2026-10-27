import pandas as pd
import numpy as np
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'strategy_engine'))
from strategies import moving_average_crossover, rsi_strategy, macd_strategy


def backtest_strategy(df, initial_capital=100000):
    """
    Simulates trading based on BUY/SELL signals.
    Goes all-in on BUY, exits fully on SELL (no shorting, no leverage).
    """
    capital = initial_capital
    shares = 0
    position = None
    entry_price = 0
    trades = []
    equity_curve = [initial_capital]

    for i in range(len(df)):
        price = df['Close'].iloc[i]
        signal = df['Signal'].iloc[i]

        if signal == 'BUY' and position is None:
            shares = capital / price
            entry_price = price
            position = 'LONG'
            capital = 0

        elif signal == 'SELL' and position == 'LONG':
            capital = shares * price
            profit_pct = ((price - entry_price) / entry_price) * 100
            trades.append(profit_pct)
            shares = 0
            position = None

        current_equity = capital if position is None else shares * price
        equity_curve.append(current_equity)

    if position == 'LONG':
        final_price = df['Close'].iloc[-1]
        capital = shares * final_price
        profit_pct = ((final_price - entry_price) / entry_price) * 100
        trades.append(profit_pct)

    final_value = capital if capital > 0 else equity_curve[-1]

    total_trades = len(trades)
    winning_trades = len([t for t in trades if t > 0])
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
    total_return = ((final_value - initial_capital) / initial_capital) * 100

    equity_series = pd.Series(equity_curve)
    running_max = equity_series.cummax()
    drawdown = (equity_series - running_max) / running_max * 100
    max_drawdown = drawdown.min()

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "win_rate": round(win_rate, 2),
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "final_value": round(final_value, 2)
    }


def run_all_backtests(ticker):
    file_path = f"data/processed/{ticker.replace('.', '_')}_data.csv"
    df = pd.read_csv(file_path)

    strategies = {
        "MA Crossover": moving_average_crossover(df),
        "RSI Strategy": rsi_strategy(df),
        "MACD Strategy": macd_strategy(df)
    }

    return {name: backtest_strategy(strat_df) for name, strat_df in strategies.items()}


if __name__ == "__main__":
    tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]

    print("=" * 70)
    print("BACKTESTING ALL STRATEGIES")
    print("=" * 70)

    summary_rows = []

    for ticker in tickers:
        print(f"\n--- {ticker} ---")
        results = run_all_backtests(ticker)
        for strategy_name, metrics in results.items():
            print(f"{strategy_name:15s} | Trades: {metrics['total_trades']:3d} | "
                  f"Win Rate: {metrics['win_rate']:6.2f}% | "
                  f"Return: {metrics['total_return_pct']:7.2f}% | "
                  f"Max Drawdown: {metrics['max_drawdown_pct']:7.2f}%")
            summary_rows.append({"Ticker": ticker, "Strategy": strategy_name, **metrics})

    summary_df = pd.DataFrame(summary_rows)
    os.makedirs("backtesting/results", exist_ok=True)
    summary_df.to_csv("backtesting/results/backtest_summary.csv", index=False)
    print("\n\nSummary saved to backtesting/results/backtest_summary.csv")