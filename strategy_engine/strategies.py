import pandas as pd

def moving_average_crossover(df):
    """
    BUY when MA_20 crosses above MA_50 (bullish momentum)
    SELL when MA_20 crosses below MA_50 (bearish momentum)
    """
    df = df.copy()
    df['Signal'] = 'HOLD'
    
    for i in range(1, len(df)):
        prev_ma20, prev_ma50 = df['MA_20'].iloc[i-1], df['MA_50'].iloc[i-1]
        curr_ma20, curr_ma50 = df['MA_20'].iloc[i], df['MA_50'].iloc[i]
        
        if prev_ma20 <= prev_ma50 and curr_ma20 > curr_ma50:
            df.iloc[i, df.columns.get_loc('Signal')] = 'BUY'
        elif prev_ma20 >= prev_ma50 and curr_ma20 < curr_ma50:
            df.iloc[i, df.columns.get_loc('Signal')] = 'SELL'
    
    return df


def rsi_strategy(df, period=14, overbought=70, oversold=30):
    """
    Calculates RSI (Relative Strength Index).
    BUY when RSI crosses below 30 (oversold — likely to bounce up)
    SELL when RSI crosses above 70 (overbought — likely to drop)
    """
    df = df.copy()
    delta = df['Close'].diff()
    
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    df['Signal'] = 'HOLD'
    df.loc[df['RSI'] < oversold, 'Signal'] = 'BUY'
    df.loc[df['RSI'] > overbought, 'Signal'] = 'SELL'
    
    return df


def macd_strategy(df, fast=12, slow=26, signal_period=9):
    """
    MACD Strategy.
    BUY when MACD line crosses above Signal line
    SELL when MACD line crosses below Signal line
    """
    df = df.copy()
    
    ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
    
    df['MACD'] = ema_fast - ema_slow
    df['MACD_Signal'] = df['MACD'].ewm(span=signal_period, adjust=False).mean()
    
    df['Signal'] = 'HOLD'
    for i in range(1, len(df)):
        prev_macd, prev_signal = df['MACD'].iloc[i-1], df['MACD_Signal'].iloc[i-1]
        curr_macd, curr_signal = df['MACD'].iloc[i], df['MACD_Signal'].iloc[i]
        
        if prev_macd <= prev_signal and curr_macd > curr_signal:
            df.iloc[i, df.columns.get_loc('Signal')] = 'BUY'
        elif prev_macd >= prev_signal and curr_macd < curr_signal:
            df.iloc[i, df.columns.get_loc('Signal')] = 'SELL'
    
    return df


if __name__ == "__main__":
    # Quick test using RELIANCE processed data
    df = pd.read_csv("data/processed/RELIANCE_NS_data.csv")
    
    print("=== Moving Average Crossover Signals ===")
    result = moving_average_crossover(df)
    signals = result[result['Signal'] != 'HOLD'][['Date', 'Close', 'Signal']]
    print(signals)
    
    print("\n=== RSI Strategy Signals ===")
    result2 = rsi_strategy(df)
    signals2 = result2[result2['Signal'] != 'HOLD'][['Date', 'Close', 'RSI', 'Signal']]
    print(signals2)
    
    print("\n=== MACD Strategy Signals ===")
    result3 = macd_strategy(df)
    signals3 = result3[result3['Signal'] != 'HOLD'][['Date', 'Close', 'MACD', 'Signal']]
    print(signals3)