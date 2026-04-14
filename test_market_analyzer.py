"""
Script di test per l'analizzatore dello stato del mercato
Carica dati da MetaTrader5 e fornisce un'analisi completa del mercato
"""

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
from indicators.market_analyzer import analyze_market_state, get_market_summary


def analyze_market_period(symbol, start_date, end_date, timeframe=mt5.TIMEFRAME_M15):
    """
    Analizza lo stato del mercato per un dato periodo.
    
    Args:
        symbol (str): simbolo da analizzare (es. "XAUUSD")
        start_date (datetime): data inizio
        end_date (datetime): data fine
        timeframe: timeframe di MetaTrader5 (default: M15)
    
    Returns:
        tuple: (dataframe giornaliero, summary dict)
    """
    # Scarica i dati
    rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)
    
    if rates is None or len(rates) == 0:
        raise ValueError(f"Nessun dato disponibile per {symbol}")
    
    # Converte in DataFrame
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # Analizza lo stato del mercato
    daily = analyze_market_state(df)
    summary = get_market_summary(daily)
    
    return daily, summary


# ==========================================
# ESECUZIONE
# ==========================================
if __name__ == "__main__":
    # Inizializza MetaTrader5
    mt5.initialize()
    
    symbol = "XAUUSD"
    start_date = datetime(2026, 3, 13)
    end_date = datetime(2026, 4, 15)
    
    # Esegui l'analisi
    daily, summary = analyze_market_period(symbol, start_date, end_date)
    
    # Mostra risultati
    print("\n" + "="*60)
    print(f"ANALISI: {symbol} dal {start_date.date()} al {end_date.date()}")
    print("="*60)
    print("\nDATI GIORNALIERI:")
    print(daily.to_string(index=False))
    
    print("\n" + "-"*60)
    print("RIEPILOGO")
    print("-"*60)
    for key, value in summary.items():
        print(f"{key:.<30} {value}")
    
    # Chiudi MetaTrader5
    mt5.shutdown()
