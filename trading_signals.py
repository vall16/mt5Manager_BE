# backend/app.py
from datetime import datetime
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import MetaTrader5 as mt5
from logger import log, logs

import pandas as pd
import threading
import time

import requests

router = APIRouter()

BASE_URL = "http://127.0.0.1:8080"   # API del tuo FastAPI
TRADER_ID = 1
# SYMBOL = "USDCAD"
SYMBOL = "EURUSD"
# SYMBOL = "XAUUSD"
# SYMBOL = "USDCAD"
TIMEFRAME = mt5.TIMEFRAME_M5
N_CANDLES = 50
CHECK_INTERVAL = 10  # secondi
PARAMETERS = {"EMA_short": 10, "EMA_long": 30, "RSI_period": 14}

# Stato globale del segnale
current_signal = "HOLD"
previous_signal = "HOLD"

# polling_thread = None
polling_running = False

polling_timer = None  # riferimento al Timer



# def normalize(d):
#     return {k.lower(): v for k, v in d.items()}


# =========================
# Funzioni indicatori
# =========================
def polling_loop_timer():
    global polling_timer, polling_running
    if not polling_running:
        return  # stop se il polling è stato fermato

    check_signal()
    
    # richiama se stesso dopo CHECK_INTERVAL secondi
    polling_timer = threading.Timer(CHECK_INTERVAL, polling_loop_timer)
    polling_timer.daemon = True
    polling_timer.start()

def get_data(symbol, timeframe, n_candles):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n_candles)

    if rates is None or len(rates) == 0:
        # log(f"❌ Nessun dato ricevuto da MT5 per {symbol}")

        return None

    df = pd.DataFrame(rates)

    if "time" not in df.columns:
        log(f"❌ La colonna time non esiste nel DataFrame: {df.columns}")

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
    global current_signal,previous_signal

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    positions = []   # ← SEMPRE nella prima riga

    # blocco inizialize che sembra rallentare ...
    if not mt5.initialize():
        print("Errore MT5:", mt5.last_error())
        return
    
    df = get_data(SYMBOL, TIMEFRAME, N_CANDLES)
    if df is None:
        # log(f"⚠️ get_data() ha restituito None per {SYMBOL}. Salto il ciclo.")
        return  # esce da check_signal() senza fare danni

    ema_short = compute_ema(df, PARAMETERS["EMA_short"])
    ema_long = compute_ema(df, PARAMETERS["EMA_long"])
    rsi = compute_rsi(df, PARAMETERS["RSI_period"])

    #  Determina segnale attuale
    if ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 70:

        current_signal = "BUY"
        previous_signal = current_signal  # aggiorna prima di uscire


        log("───────S-I-G-N-A-L──────────")
        log(f"🔥 [{now}] BUY signal per {SYMBOL} !")

        # 1️⃣ Recupera le posizioni correnti sullo SLAVE per vedere se c'è già il buy per lui
                
        base_url_slave = "http://127.0.0.1:9001"

        try:
                positions_url = f"{base_url_slave}/positions"
                log(f"🔹 Recupero posizioni dallo slave via {positions_url}")

                resp = requests.get(positions_url, timeout=10)
                resp.raise_for_status()
                positions = resp.json()

                if positions:
                    log("📌 Posizioni aperte sullo SLAVE:")
                    for p in positions:
                        log(f"  - Symbol: {p['symbol']}, Volume: {p['volume']}, Type: {p['type']}")
                    

                    # Se c'è già il simbolo  non inviare nuovo ordine
                    if any(p["symbol"] == SYMBOL for p in positions):
                        log(f"⚠️ Posizione {SYMBOL} già aperta sullo SLAVE. Skip BUY.")
                        
                        return

        except requests.exceptions.RequestException as e:
                log(f"❌ Errore di connessione al slave API: {e}")
                

        # 2️⃣ Controlla se c'è già una posizione aperta su XAUUSD
        if any(p.get("symbol") == SYMBOL for p in positions):
            log("⚠️ Posizione già aperta sullo SLAVE, skip invio BUY")
            return
        else:
            log(f"🚀 Invio BUY allo SLAVE")
            send_buy_to_slave()

    else:
        current_signal = "HOLD"
        log("───────S-I-G-N-A-L──────────")
        log(f"⚠️  [{now}] HOLD signal per {SYMBOL} ...")   

        # Se il segnale passa da BUY a HOLD, chiudiamo la posizione
        # Log dello stato precedente e attuale
        log(f"🔄 previous_signal = {previous_signal}, current_signal = {current_signal}")

        if previous_signal == "BUY":
            log(f"⚠️ Segnale passato da BUY a HOLD → chiudo posizione {SYMBOL} sullo SLAVE")
            close_slave_position()
     
    # Aggiorna lo stato precedente
    previous_signal = current_signal

    

# Polling in background
def polling_loop():
    log("▶️ Polling loop partito")

    while True:
        check_signal()
        time.sleep(CHECK_INTERVAL)

# chiamata da FE
# @router.post("/start_polling")
# def start_polling():
#     global polling_thread, polling_running

#     if polling_running:
#         return {"status": "already_running", "message": "Polling già attivo"}

#     polling_running = True
#     polling_thread = threading.Thread(target=polling_loop, daemon=True)
#     polling_thread.start()

#     log("▶️ Polling avviato manualmente dal frontend!")
#     return {"status": "started"}


@router.post("/start_polling")
def start_polling():
    global polling_running, polling_timer

    if polling_running:
        return {"status": "already_running", "message": "Polling già attivo"}

    polling_running = True
    polling_loop_timer()  # avvia subito il ciclo

    log("▶️ Polling avviato manualmente dal frontend!")
    return {"status": "started"}

@router.post("/stop_polling")
def stop_polling():
    global polling_running, polling_timer

    if not polling_running:
        return {"status": "not_running", "message": "Polling non è attivo"}

    polling_running = False

    if polling_timer:
        polling_timer.cancel()
        polling_timer = None

    log("⏹️ Polling fermato manualmente dal frontend!")
    return {"status": "stopped"}

# threading.Thread(target=start_polling, daemon=True).start()
# @router.on_event("startup")
# def on_startup():
#     # Lancia il polling in un thread separato solo all'avvio del server
#     threading.Thread(target=start_polling, daemon=True).start()
#     print("✅ Polling thread avviato all'avvio del server")


# Endpoint per il frontend
@router.get("/signal")
def get_signal():
    return {"signal": current_signal}

def send_buy_to_slave():
    url = f"{BASE_URL}/db/traders/{TRADER_ID}/open_order_on_slave"
    payload = {
         "trader_id": TRADER_ID,
        "order_type": "buy",
        "volume": 0.10,
        "symbol": SYMBOL        
    }

    log(f"📤 Invio BUY [symbol={SYMBOL}] allo SLAVE → {url} ")

    try:
        resp = requests.post(url, json=payload, timeout=10)
        log(f"📥 Risposta SLAVE: {resp.text}")
    except requests.RequestException as e:
        log(f"❌ Errore invio ordine: {e}")

def close_slave_position():
    url = f"{BASE_URL}/db/traders/{TRADER_ID}/close_order_on_slave"
    payload = {"symbol": SYMBOL,"trader_id": TRADER_ID}
    log(f"📤 Invio richiesta chiusura posizione [symbol={SYMBOL}] allo SLAVE → {url}")

    try:
        resp = requests.post(url, json=payload, timeout=10)
        log(f"📥 Risposta chiusura SLAVE: {resp.text}")
    except requests.RequestException as e:
        log(f"❌ Errore chiusura posizione: {e}")

