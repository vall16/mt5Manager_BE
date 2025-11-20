# backend/app.py
from datetime import datetime
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import MetaTrader5 as mt5
import pandas as pd
import threading
import time

import requests

router = APIRouter()

SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
N_CANDLES = 50
CHECK_INTERVAL = 60  # secondi
PARAMETERS = {"EMA_short": 10, "EMA_long": 30, "RSI_period": 14}

# Stato globale del segnale
current_signal = "HOLD"
# buy_executed=False
# Stato globale del segnale
# current_signal = "HOLD"
# last_signal = "HOLD"   # <- aggiunto
# buy_executed = False


# =========================
# Funzioni indicatori
# =========================
def get_data(symbol, timeframe, n_candles):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n_candles)

    if rates is None or len(rates) == 0:
        print("❌ Nessun dato ricevuto da MT5 per", symbol)
        return None

    df = pd.DataFrame(rates)

    if "time" not in df.columns:
        print("❌ La colonna time non esiste nel DataFrame:", df.columns)
        return None

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

    #  Determina segnale attuale
    if ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 70:
        current_signal = "BUY"
    else:
        current_signal = "HOLD"

    # 1️⃣ Recupera le posizioni correnti sullo SLAVE
        
    # base_url_slave = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    # base_url_slave = "http://127.0.0.1:9001"

    # try:
    #     positions_url = f"{base_url_slave}/positions"
    #     print(f"🔹 Recupero posizioni dallo slave via {positions_url}")

    #     resp = requests.get(positions_url, timeout=10)
    #     resp.raise_for_status()
    #     positions = resp.json()

    #     if positions:
    #         print("📌 Posizioni aperte sullo SLAVE:")
    #         for p in positions:
    #             print(f"  - Symbol: {p['symbol']}, Volume: {p['volume']}, Type: {p['type']}, Price: {p['price']}, Ticket: {p['ticket']}")
            
    #         # Se c'è già il simbolo XAUUSD non inviare nuovo ordine
    #         if any(p["symbol"] == SYMBOL for p in positions):
    #             print(f"⚠️ Posizione {SYMBOL} già aperta sullo SLAVE. Skip BUY.")
    #             return

    # except requests.exceptions.RequestException as e:
    #     print(f"❌ Errore di connessione al slave API: {e}")
    #     raise HTTPException(status_code=500, detail=f"Errore connessione al slave: {str(e)}")


    # # 2️⃣ Controlla se c'è già una posizione aperta su XAUUSD
    # if any(p.get("symbol") == "XAUUSD" for p in positions):
    #     print("⚠️ Posizione su XAUUSD già aperta sullo SLAVE, skip invio BUY")
    #     return
    
    # print(f"🚀 Segnale {current_signal}! ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    # print(f"🔍 Debug condizione current_signal={current_signal}")


    # Se il segnale cambia da HOLD → BUY
    if current_signal == "BUY":
        
            print("🔥 Invio BUY allo slave")

            send_buy_to_slave()
            
    # Se il segnale diventa HOLD dopo un BUY, resettiamo il flag
    if current_signal == "HOLD":
        
        print("⚠️ Segnale HOLD, reset buy_executed")

        # Qui puoi inviare eventualmente close_buy allo SLAVE
        # send_close_buy_to_slave()
    


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

TRADER_ID = 1  # <-- cambia questo col tuo trader reale
BASE_URL = "http://127.0.0.1:8080"   # API del tuo FastAPI

def send_buy_to_slave():
    url = f"{BASE_URL}/db/traders/{TRADER_ID}/open_order_on_slave"
    payload = {
        "order_type": "buy",
        "volume": 0.10
    }

    print(f"📤 Invio BUY allo SLAVE → {url}")

    try:
        resp = requests.post(url, json=payload, timeout=10)
        print("📥 Risposta SLAVE:", resp.text)
    except Exception as e:
        print("❌ Errore invio ordine:", e)

