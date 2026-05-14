"""
Script di test per l'analizzatore dello stato del mercato
Carica dati da MetaTrader5 e fornisce un'analisi completa del mercato
"""

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
from indicators.market_analyzer import analyze_market_state, get_market_summary


def get_atr_now(symbol, minutes=120):
    import MetaTrader5 as mt5
    import pandas as pd
    from datetime import datetime, timedelta

    end = datetime.now()
    start = end - timedelta(minutes=minutes)

    rates = mt5.copy_rates_range(
        symbol,
        mt5.TIMEFRAME_M1,
        start,
        end
    )

    df = pd.DataFrame(rates)
    if df.empty:
        return None

    df['tr'] = df.apply(lambda r: max(
        r['high'] - r['low'],
        abs(r['high'] - r['close']),
        abs(r['low'] - r['close'])
    ), axis=1)

    return df['tr'].mean()
# per l'atr giornaliero...
def get_live_daily_atr(symbol, period=14):
    # storico daily
    d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, period)
    d1_df = pd.DataFrame(d1)

    # dati intraday di oggi (M1)
    # VOLATILITA' PER MINUTO
    today = datetime.now().date()
    m1 = mt5.copy_rates_range(
        symbol,
        mt5.TIMEFRAME_M1,
        datetime(today.year, today.month, today.day),
        datetime.now()
    )
    m1_df = pd.DataFrame(m1)

    if len(m1_df) == 0:
        return None

    # costruisci la candela daily "live"
    high = m1_df['high'].max()
    low = m1_df['low'].min()
    close = m1_df.iloc[-1]['close']
    prev_close = d1_df.iloc[-1]['close']

    tr_today = max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close)
    )

    # ATR = media TR (storico + oggi)
    trs = list(
        (d1_df['high'] - d1_df['low'])
    )
    trs.append(tr_today)

    atr = pd.Series(trs).rolling(period).mean().iloc[-1]

    return atr

def get_atr_state(symbol, period=14):
    # ===== storico daily =====
    d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, period + 1)
    d1_df = pd.DataFrame(d1)

    d1_df['prev_close'] = d1_df['close'].shift(1)

    d1_df['tr'] = d1_df.apply(lambda row: max(
        row['high'] - row['low'],
        abs(row['high'] - row['prev_close']),
        abs(row['low'] - row['prev_close'])
    ), axis=1)

    atr_hist = d1_df['tr'].rolling(period).mean().iloc[-1]

    # ===== ATR LIVE =====
    atr_live = get_live_daily_atr(symbol, period)

    if atr_live is None or atr_hist is None:
        return None

    ratio = atr_live / atr_hist

    # ===== CLASSIFICAZIONE =====
    if ratio < 0.8:
        state = "LOW"
    elif ratio < 1.2:
        state = "NORMAL"
    else:
        state = "HIGH"

    return {
        "atr_live": atr_live,
        "atr_hist": atr_hist,
        "ratio": ratio,
        "state": state
    }

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
    start_date = datetime(2026, 1, 1)
    end_date = datetime(2026, 5, 14)
    
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

atr_info = get_atr_state(symbol)

print("\n" + "-"*60)
print("ATR STATE LIVE")
print("-"*60)

if atr_info:
    print(f"ATR live: {atr_info['atr_live']:.2f}")
    print(f"ATR medio: {atr_info['atr_hist']:.2f}")
    print(f"Ratio: {atr_info['ratio']:.2f}")
    print(f"STATE: {atr_info['state']}")
else:
    print("ATR non disponibile")    
    # Chiudi MetaTrader5
    mt5.shutdown()
