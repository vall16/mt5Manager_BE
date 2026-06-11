"""
Script di test per l'analizzatore dello stato del mercato
Include:
- analisi daily
- ATR regime (daily)
- ATR NOW (intraday M1)
"""

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
from indicators.market_analyzer import analyze_market_state, get_market_summary


# =========================================================
# ATR NOW (intraday rolling M1)
# =========================================================
def get_atr_now(symbol, minutes=120):
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

def get_atr_m5(symbol, period=14):
    rates = mt5.copy_rates_from_pos(
        symbol,
        mt5.TIMEFRAME_M5,
        0,
        period + 50  # buffer sicurezza
    )

    df = pd.DataFrame(rates)
    if df.empty:
        return None

    df['prev_close'] = df['close'].shift(1)

    df['tr'] = df.apply(lambda r: max(
        r['high'] - r['low'],
        abs(r['high'] - r['prev_close']),
        abs(r['low'] - r['prev_close'])
    ), axis=1)

    atr = df['tr'].rolling(period).mean().iloc[-1]

    return atr

# =========================================================
# ATR DAILY LIVE + HIST
# =========================================================
def get_live_daily_atr(symbol, period=14):

    d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, period + 1)
    d1_df = pd.DataFrame(d1)

    d1_df['prev_close'] = d1_df['close'].shift(1)

    d1_df['tr'] = d1_df.apply(lambda row: max(
        row['high'] - row['low'],
        abs(row['high'] - row['prev_close']),
        abs(row['low'] - row['prev_close'])
    ), axis=1)

    atr_hist = d1_df['tr'].rolling(period).mean().iloc[-1]

    # daily live costruita da M1
    today = datetime.now().date()

    m1 = mt5.copy_rates_range(
        symbol,
        mt5.TIMEFRAME_M1,
        datetime(today.year, today.month, today.day),
        datetime.now()
    )

    m1_df = pd.DataFrame(m1)

    if m1_df.empty:
        return None

    high = m1_df['high'].max()
    low = m1_df['low'].min()
    close = m1_df.iloc[-1]['close']
    prev_close = d1_df.iloc[-1]['close']

    tr_today = max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close)
    )

    trs = list(d1_df['tr'].dropna())
    trs.append(tr_today)

    atr_live = pd.Series(trs).rolling(period).mean().iloc[-1]

    ratio = atr_live / atr_hist

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


def get_rolling_max_atr_m15(symbol, period=14):
    # 1. Definiamo quante candele M15 ci sono in una giornata intera (24 ore * 4 candele/ora = 96)
    # Aggiungiamo un buffer generoso (4 * period) per stabilizzare l'ATR di Wilder
    buffer_candles = period * 4
    total_candles_to_request = 96 + buffer_candles
    
    # Recuperiamo le candele a ritroso partendo da ORA (orario del terminale)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, total_candles_to_request)
    
    if rates is None or len(rates) == 0:
        return None
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    # 2. Calcoliamo l'ATR Wilder
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = abs(df['low'] - df['close'].shift(1))
    df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    df['ATR'] = df['TR'].ewm(alpha=1/period, adjust=False).mean()
    
    # 3. Identifichiamo l'inizio della giornata odierna secondo il tempo del BROKER
    # Prendiamo l'ultimo timestamp disponibile nel df (che è la candela corrente) e isoliamo la data
    current_broker_time = df['time'].iloc[-1]
    start_of_today = pd.to_datetime(current_broker_time.date())
    
    # Filtriamo solo le candele di OGGI
    df_today = df[df['time'] >= start_of_today]
    
    if df_today.empty:
        return float(df['ATR'].iloc[-1])
        
    max_atr_today = df_today['ATR'].max()
    return float(max_atr_today)


# =========================================================
# ANALISI PERIODO
# =========================================================
def analyze_market_period(symbol, start_date, end_date, timeframe=mt5.TIMEFRAME_M15):

    rates = mt5.copy_rates_range(symbol, timeframe, start_date, end_date)

    if rates is None or len(rates) == 0:
        raise ValueError(f"Nessun dato disponibile per {symbol}")

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')

    daily = analyze_market_state(df)
    summary = get_market_summary(daily)

    return daily, summary


# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":

    mt5.initialize()

    symbol = "XAUUSD"
    start_date = datetime(2026, 1, 1)
    end_date = datetime(2026, 9, 10)

    # =========================
    # ANALISI STORICA
    # =========================
    daily, summary = analyze_market_period(symbol, start_date, end_date)

    print("\n" + "="*60)
    print(f"ANALISI: {symbol}")
    print("="*60)

    print("\nDATI GIORNALIERI:")
    print(daily.to_string(index=False))

    print("\nRIEPILOGO:")
    for k, v in summary.items():
        print(f"{k:.<30} {v}")


    # =========================
    # ATR LIVE (REGIME)
    # =========================
    atr_info = get_live_daily_atr(symbol)

    # Recupero del massimo ATR M15 registrato oggi
    max_atr_m15 = get_rolling_max_atr_m15(symbol)


    # Stampa del Massimo ATR M15 intraday
    # if max_atr_m15 is not None:
    #     print(f"Max ATR Today (M15): {max_atr_m15:.2f}")
    # else:
    #     print("Max ATR Today (M15): Non disponibile")

    # =========================
    # ATR NOW (M1)
    # =========================
    atr_now = get_atr_now(symbol)
    atr_m5 = get_atr_m5(symbol)


    # =========================
    # MAX ATR TODAY
    # =========================
    max_atr_today= get_live_daily_atr(symbol)

    print("\n" + "-"*60)
    print("VOLATILITY ENGINE")
    print("-"*60)


    if atr_info:
        print(f"ATR daily live : {atr_info['atr_live']:.2f}")
        print(f"ATR daily hist : {atr_info['atr_hist']:.2f}")
        print(f"Ratio          : {atr_info['ratio']:.2f}")
        print(f"State          : {atr_info['state']}")
    else:
        print("ATR daily non disponibile")


    print("\n" + "-"*60)
    print("ATR NOW (intraday M1)")
    print("-"*60)

    if atr_now:
        print(f"ATR now (M1)   : {atr_now:.2f}")
    else:
        print("ATR NOW non disponibile")

    if atr_m5:
        print(f"ATR  (M5)   : {atr_m5:.2f}")
    else:
        print("ATR M5 non disponibile")

    # Stampa del Massimo ATR M15 intraday
    if max_atr_m15 is not None:
        print(f"Max ATR Today (M15): {max_atr_m15:.2f}")
    else:
        print("Max ATR Today (M15): Non disponibile")


    
    
        #     ATR M5 è il tuo “vero cervello decisionale”

        # E puoi fare:

        # LOW → < 5–6$
        # NORMAL → 6–12$
        # HIGH → > 12–15$


    mt5.shutdown()