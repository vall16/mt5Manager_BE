import pandas as pd
import numpy as np

def compute_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def compute_rsi(df, period):
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
# aggiunti da poco
def compute_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    return macd, macd_signal

def compute_atr(df, period=14):
    df['tr'] = df['high'] - df['low']
    atr = df['tr'].rolling(period).mean()
    return atr
def compute_bollinger(df, period=20, num_std=2):
    """
    Calcola le Bollinger Bands.
    
    Args:
        df (DataFrame): dati OHLC con colonna 'close'
        period (int): periodo della SMA
        num_std (float): numero di deviazioni standard per le bande

    Returns:
        tuple: (SMA, upper_band, lower_band) come pd.Series
    """
    sma = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()

    upper_band = sma + (std * num_std)
    lower_band = sma - (std * num_std)

    return sma, upper_band, lower_band

import numpy as np

def compute_hma(df, period=16):
    """
    Calcola l'Hull Moving Average (HMA) su una serie 'close'.

    Args:
        df (DataFrame): dati OHLC con colonna 'close'
        period (int): periodo della HMA (tipico: 16, 20, 21)

    Returns:
        pd.Series: serie HMA
    """
    half_period = int(period / 2)
    sqrt_period = int(np.sqrt(period))

    # WMA ponderata
    def wma(series, n):
        weights = np.arange(1, n + 1)
        return series.rolling(n).apply(lambda x: np.dot(x, weights)/weights.sum(), raw=True)

    wma_half = wma(df['close'], half_period)
    wma_full = wma(df['close'], period)

    hma = wma(2 * wma_half - wma_full, sqrt_period)
    return hma

def compute_adx(df, period=14):
    """
    Calcola l'Average Directional Index (ADX) e i componenti +DI e -DI.

    Args:
        df (DataFrame): dati OHLC con colonne 'high', 'low', 'close'
        period (int): periodo per il calcolo (default=14)

    Returns:
        pd.DataFrame: colonne ['ADX', '+DI', '-DI']
    """
    high = df['high']
    low = df['low']
    close = df['close']

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # +DM e -DM
    up_move = high - high.shift()
    down_move = low.shift() - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    # Smoothed TR, +DM, -DM
    atr = tr.rolling(period).mean()
    plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
    minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(period).mean()

    return pd.DataFrame({'ADX': adx, '+DI': plus_di, '-DI': minus_di})
