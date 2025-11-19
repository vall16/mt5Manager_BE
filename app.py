# backend/app.py
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

router = APIRouter()

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
N_CANDLES = 50
CHECK_INTERVAL = 10  # secondi
PARAMETERS = {"EMA_short": 10, "EMA_long": 30, "RSI_period": 14}

# Stato globale del segnale
current_signal = "HOLD"

# =========================
# Funzioni indicatori
# =========================
def get_data(symbol, timeframe, n):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df

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

def check_signal():
    global current_signal
    if not mt5.initialize():
        print("Errore MT5:", mt5.last_error())
        return
    df = get_data(SYMBOL, TIMEFRAME, N_CANDLES)
    ema_short = compute_ema(df, PARAMETERS["EMA_short"])
    ema_long = compute_ema(df, PARAMETERS["EMA_long"])
    rsi = compute_rsi(df, PARAMETERS["RSI_period"])
    
    if ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 70:
        current_signal = "BUY"
        # Qui puoi inserire il codice per aprire la posizione su MT5
        print("🚀 Segnale BUY!")
    else:
        current_signal = "HOLD"

# Polling in background
def start_polling():
    while True:
        check_signal()
        time.sleep(CHECK_INTERVAL)

threading.Thread(target=start_polling, daemon=True).start()

# Endpoint per il frontend
@router.get("/signal")
def get_signal():
    return {"signal": current_signal}
