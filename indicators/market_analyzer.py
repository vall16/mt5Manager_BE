"""
Market State Analyzer
Analizza il mercato per determinare lo stato (TREND, RANGE, CHAOS, UNCLEAR)
e calcola un score di tradabilità.
"""

import pandas as pd
import numpy as np


def compute_atr_wilder(df, period=14):
    """
    Calcola l'ATR con True Range method (Wilder style).
    
    Args:
        df (DataFrame): dati OHLC con colonne 'high', 'low', 'close'
        period (int): periodo per il calcolo (default=14)
    
    Returns:
        pd.Series: serie ATR
    """
    df = df.copy()
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = abs(df['low'] - df['close'].shift(1))
    
    df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    df['ATR'] = df['TR'].ewm(alpha=1/period, adjust=False).mean()
    
    return df['ATR']


def aggregate_daily_market_data(df):
    """
    Aggrega i dati intraday a dati giornalieri con ATR e range.
    
    Args:
        df (DataFrame): dati OHLC con colonna 'time' (datetime)
    
    Returns:
        pd.DataFrame: dati giornalieri aggregati
    """
    df = df.copy()
    df['date'] = df['time'].dt.date
    
    daily = df.groupby('date').agg({
        'ATR': 'last',
        'high': 'max',
        'low': 'min'
    }).reset_index()
    
    daily['range'] = daily['high'] - daily['low']
    
    return daily


def classify_market_state(row, atr_mean, atr_std):
    """
    Classifica lo stato del mercato basato su ATR normalizzato e range.
    
    Args:
        row (pd.Series): riga con 'ATR' e 'range'
        atr_mean (float): media dell'ATR
        atr_std (float): deviazione standard dell'ATR
    
    Returns:
        str: stato del mercato (TREND, RANGE, CHAOS, UNCLEAR)
    """
    atr = row['ATR']
    rng = row['range']
    
    atr_score = (atr - atr_mean) / atr_std if atr_std != 0 else 0
    
    if atr_score > 1 and rng > atr * 3:
        return "CHAOS"
    elif atr_score > 0.3 and rng > atr * 2:
        return "TREND"
    elif atr_score < 0.3:
        return "RANGE"
    else:
        return "UNCLEAR"


def get_tradability_score(market_state):
    """
    Assegna un score di tradabilità per ogni stato di mercato.
    
    Args:
        market_state (str): stato del mercato
    
    Returns:
        float: score di tradabilità (0-1)
    """
    scores = {
        "TREND": 1.0,
        "RANGE": 0.5,
        "CHAOS": 0.0,
        "UNCLEAR": 0.2
    }
    return scores.get(market_state, 0.2)


def analyze_market_state(df):
    """
    Funzione principale: analizza lo stato del mercato e ritorna un DataFrame
    con classificazioni e score.
    
    Args:
        df (DataFrame): dati OHLC con 'high', 'low', 'close', 'time' (datetime)
    
    Returns:
        pd.DataFrame: dati giornalieri con 'market_state' e 'tradability_score'
    """
    df = df.copy()
    
    # Calcola ATR
    df['ATR'] = compute_atr_wilder(df, period=14)
    
    # Aggrega a dati giornalieri
    daily = aggregate_daily_market_data(df)
    
    # Normalizza ATR per classificazione
    atr_mean = daily['ATR'].mean()
    atr_std = daily['ATR'].std()
    
    # Classifica stato del mercato
    daily['market_state'] = daily.apply(
        lambda row: classify_market_state(row, atr_mean, atr_std),
        axis=1
    )
    
    # Calcola tradability score
    daily['tradability_score'] = daily['market_state'].apply(get_tradability_score)
    
    return daily


def get_market_summary(daily_df):
    """
    Ritorna un riepilogo statistico dello stato del mercato.
    
    Args:
        daily_df (DataFrame): DataFrame giornaliero da analyze_market_state()
    
    Returns:
        dict: riepilogo con statistiche
    """
    return {
        "total_days": len(daily_df),
        "trend_days": int((daily_df['market_state'] == "TREND").sum()),
        "range_days": int((daily_df['market_state'] == "RANGE").sum()),
        "chaos_days": int((daily_df['market_state'] == "CHAOS").sum()),
        "unclear_days": int((daily_df['market_state'] == "UNCLEAR").sum()),
        "avg_atr": float(daily_df['ATR'].mean()),
        "avg_tradability_score": float(daily_df['tradability_score'].mean())
    }
