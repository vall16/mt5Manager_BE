# backend/app.py
from datetime import datetime
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import MetaTrader5 as mt5
from logger import log, logs
from logger import safe_get
from db import get_trader, get_connection
from models import (
    Trader
)
import pandas as pd
import threading
import time

import requests

router = APIRouter()

BASE_URL = "http://127.0.0.1:8080"   # API del tuo FastAPI
TRADER_ID = 1
CURRENT_TRADER: Trader | None = None
# SYMBOL = "USDCAD"
# SYMBOL = "EURUSD"
# SYMBOL = "MSFT"
SYMBOL = "XAUUSD"
TIMEFRAME = mt5.TIMEFRAME_M5
N_CANDLES = 50
CHECK_INTERVAL = 10  # secondi
PARAMETERS = {"EMA_short": 5, "EMA_long": 15, "RSI_period": 14}

# Stato globale del segnale
current_signal = "HOLD"
previous_signal = "HOLD"

# polling_thread = None
polling_running = False

polling_timer = None  # riferimento al Timer

base_url_slave = "http://127.0.0.1:9001"


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

def check_signal_new():
    global current_signal, previous_signal

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    positions = []

    if not mt5.initialize():
        print("Errore MT5:", mt5.last_error())
        return

    df = get_data(SYMBOL, TIMEFRAME, N_CANDLES)
    if df is None:
        return

    # Calcoli indicatori
    ema_short = compute_ema(df, PARAMETERS["EMA_short"])
    ema_long = compute_ema(df, PARAMETERS["EMA_long"])
    rsi = compute_rsi(df, PARAMETERS["RSI_period"])
    macd, macd_signal = compute_macd(df)
    atr = compute_atr(df)

    # Calcolo pendenze EMA
    ema_slope = ema_short.iloc[-1] - ema_short.iloc[-2]
    ema_long_slope = ema_long.iloc[-1] - ema_long.iloc[-2]

    # Parametri di soglia
    rsi_threshold = 60
    atr_threshold = df['close'].std() * 0.1  # esempio, puoi personalizzare

    # =======================
    # Condizione BUY più acuminata
    # =======================
    if (ema_short.iloc[-1] > ema_long.iloc[-1] and
        ema_slope > 0 and
        ema_long_slope > 0 and
        rsi.iloc[-1] < rsi_threshold and
        macd.iloc[-1] > macd_signal.iloc[-1] and
        atr.iloc[-1] > atr_threshold):

        current_signal = "BUY"
        previous_signal = current_signal

        log("───────S-I-G-N-A-L──────────")
        log(f"🔥🔥🔥 [{now}] BUY signal per {SYMBOL} !")

        # Recupera posizioni SLAVE
        try:
            positions_url = f"{base_url_slave}/positions"
            log(f"🔹 Recupero posizioni dallo slave via {positions_url}")
            # resp = (positions_url, timeout=10)
            resp = safe_get(positions_url, timeout=10)
            resp.raise_for_status()
            positions = resp.json()

            if positions:
                log("📌 Posizioni aperte sullo SLAVE:")
                for p in positions:
                    log(f"  - Symbol: {p['symbol']}, Volume: {p['volume']}, Type: {p['type']}")

                if any(p["symbol"] == SYMBOL for p in positions):
                    log(f"⚠️ Posizione {SYMBOL} già aperta sullo SLAVE. Skip BUY.")
                    return

        except requests.exceptions.RequestException as e:
            log(f"❌ Errore di connessione al slave API: {e}")

        # Invio BUY allo slave
        log(f"🚀 Invio BUY allo SLAVE")
        send_buy_to_slave()

    else:
        current_signal = "HOLD"
        log("───────S-I-G-N-A-L──────────")
        log(f"⚠️  [{now}] HOLD signal per {SYMBOL} ...")

        # Se il segnale passa da BUY a HOLD, chiudi posizione
        log(f"🔄 previous_signal = {previous_signal}, current_signal = {current_signal}")
        if previous_signal == "BUY":
            log(f"⚠️ Segnale passato da BUY a HOLD → chiudo posizione {SYMBOL} sullo SLAVE")
            close_slave_position()

    previous_signal = current_signal


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
        
        log(f"🔥🔥🔥 [{now}] BUY signal per {SYMBOL} !")


        # 1️⃣ Recupera le posizioni correnti sullo SLAVE per vedere se c'è già il buy per lui
                
        # base_url_slave = "http://127.0.0.1:9001"

        try:
                positions_url = f"{base_url_slave}/positions"
                log(f"🔹 Recupero posizioni dallo slave via {positions_url}")

                resp = safe_get(positions_url, timeout=10)

                # ⛔ SE SAFE_GET FALLISCE → resp è None → esci subito
                if resp is None:
                    log("❌ Slave offline → esco da check_signal()")
                    return



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
        # send_buy_to_slave()
        current_signal = "HOLD"
        log("───────S-I-G-N-A-L──────────")
        # log(f"⚠️  [{now}] HOLD signal per {SYMBOL} ...")   
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


@router.post("/start_polling")
def start_polling(trader:Trader):
    
    global polling_running, polling_timer,CURRENT_TRADER, CHECK_INTERVAL

    print(">>> start_polling CHIAMATO, trader =", trader)

    # Salva il trader globale
    CURRENT_TRADER = trader

    try:
        CHECK_INTERVAL = int(trader.customSignalInterval)
    except:
        CHECK_INTERVAL = 2


    print(CHECK_INTERVAL)

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

    # conn = get_connection()
    # if not conn:
    #     raise HTTPException(status_code=500, detail="Database connection failed")

    # cursor = conn.cursor(dictionary=True)
    # # Recupera il trader dal DB
    # trader = get_trader(cursor,TRADER_ID)
    # if not trader:
    #     log(f"❌ Trader {TRADER_ID} non trovato")
    #     return
    
    # sl_raw = trader.get("sl")
    # stop_loss = float(sl_raw) if sl_raw is not None else None
    info_url = f"{base_url_slave}/symbol_info/{SYMBOL}"
    log(f"🔍 Richiedo info simbolo allo slave: {info_url}")
    
    resp = safe_get(info_url, timeout=10)

    sym_info = resp.json()

    # Prendi lo stop loss dal trader, se presente
     # 🔹 2️⃣ Recupero tick dal server slave via API
    tick_url = f"{base_url_slave}/symbol_tick/{SYMBOL}"
    log(f"📡 Richiedo tick allo slave: {tick_url}")

            
    resp_tick = safe_get(tick_url, timeout=10)
    if resp_tick.status_code != 200:
        log(f"⚠️ Nessun tick disponibile per {SYMBOL} dallo slave: {resp_tick.text}")
        # continue

    tick = resp_tick.json()
    if not tick or "bid" not in tick or "ask" not in tick:
        log(f"⚠️ Tick incompleto o non valido per {SYMBOL}: {tick}")
        # continue

    log(f"✅ Tick ricevuto per {SYMBOL}: bid={tick['bid']}, ask={tick['ask']}")

            # --- CALCOLO SL IN PIP ---
    sl_pips =  CURRENT_TRADER.sl
    # valore pip inserito dal trader nell'app (es. 10)

    if sl_pips and float(sl_pips) > 0:
        pip_value = float(sym_info.get("point"))  # valore del singolo punto del simbolo
        sl_distance = float(sl_pips) * pip_value

        # if order_type == "buy":
            # SL sotto il prezzo ask
        calculated_sl = tick["ask"] - sl_distance
        # else:
        #     # SL sopra il prezzo bid (per SELL)
        #     calculated_sl = tick["bid"] + sl_distance
    else:
        calculated_sl = None  # se sl=0 non imposta SL

    tp_pips = CURRENT_TRADER.tp  # pips inseriti dall'app
    pip_value = float(sym_info.get("point"))

    if tp_pips and float(tp_pips) > 0:
        tp_distance = float(tp_pips) * pip_value
        # if order_type == "buy":
        calculated_tp = tick["ask"] + tp_distance
        # else:
        #     calculated_tp = tick["bid"] - tp_distance
    else:
        calculated_tp = None  # se TP=0 non impostare TP


    sl_value = calculated_sl
    tp_value = calculated_tp
    trader_id = CURRENT_TRADER.id

    log(f"SL = {sl_value}, TP = {tp_value}")

    url = f"{BASE_URL}/db/traders/{trader_id}/open_order_on_slave"
    payload = {
         "trader_id": trader_id,
        "order_type": "buy",
        "volume": 0.10,
        "symbol": SYMBOL,
        "sl":sl_value,
        "tp": tp_value,

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

