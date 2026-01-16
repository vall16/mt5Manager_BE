from datetime import datetime
import json
import os
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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
from indicators import (
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_atr,
    compute_bollinger,
    compute_hma,
    compute_adx
)


HOST = os.getenv("API_HOST")  # default localhost
PORT = int(os.getenv("API_PORT"))    # default 8080

BASE_URL = f"http://{HOST}:{PORT}"         # costruisce automaticamente l'URL
TRADER_ID = 1
CURRENT_TRADER: Trader | None = None
CHOSEN_TRADESIGNAL = ""
SIGNAL_HANDLERS = {}  # Inizializza vuoto

BASE_URL_SLAVE =""
# TIMEFRAME = mt5.TIMEFRAME_M5
TIMEFRAME = 5
N_CANDLES = 50
CHECK_INTERVAL = 10  # secondi
PARAMETERS = {"EMA_short": 5, "EMA_long": 15, "RSI_period": 14}

# 1. Definisci SYMBOL in alto
SYMBOL = "XAUUSD"
# =========================
# Multi-instance polling
# =========================
active_pollings: dict[int, threading.Thread] = {}
polling_flags: dict[int, bool] = {}  # True = running, False = stopped

def get_data(symbol, timeframe, n_candles, agent_url):
    """
    Recupera i dati storici chiamando l'API remota mt5_api.
    
    :param symbol: str, es. "EURUSD"
    :param timeframe: int, costante MT5 (es. 15 per M15)
    :param n_candles: int, numero di candele
    :param agent_url: str, URL base dell'agente (es. "http://1.2.3.4:5000")
    """
    # Endpoint che abbiamo aggiunto a mt5_api.py
    url = f"{agent_url}/get_rates"
    
    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "n_candles": n_candles
    }

    try:
        # Timeout di 30s coerente con le tue mt5_routes (init-mt5/login)
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code != 200:
            # log(f"❌ Errore API {response.status_code}: {response.text}")
            return None

        data = response.json()
        rates = data.get("rates", [])

        if not rates:
            # log(f"❌ Nessun dato ricevuto da API per {symbol}")
            return None

        # Creiamo il DataFrame dalla lista di dizionari ricevuta
        df = pd.DataFrame(rates)

        if "time" not in df.columns:
            # log(f"❌ La colonna time non esiste nel DataFrame: {df.columns}")
            return None

        # Conversione corretta del timestamp Unix in datetime
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        return df

    except Exception as e:
        # log(f"❌ Eccezione durante la chiamata a {url}: {e}")
        return None


def polling_worker(trader: Trader):
    trader_id = trader.id
    log(f"▶️ Polling worker avviato per trader {trader_id}")

    # Thread-local variables
    symbol = trader.selectedSymbol
    interval = int(trader.customSignalInterval or 2)
    base_url_slave = f"http://{trader.slave_ip}:{trader.slave_port}"
    previous_signal = "HOLD"
    current_signal = "HOLD"

    while polling_flags.get(trader_id, False):
        # Qui usiamo check_signal_super ma isolato per questo trader
        try:
            check_signal_super_multi(trader, symbol, base_url_slave, previous_signal, current_signal)
        except Exception as e:
            log(f"❌ Errore polling trader {trader_id}: {e}")
        time.sleep(interval)

    log(f"⏹️ Polling worker fermato per trader {trader_id}")

def start_polling_multi(trader: Trader):
    trader_id = trader.id

    if polling_flags.get(trader_id):
        return {"status": "already_running", "message": f"Polling già attivo per trader {trader_id}"}

    polling_flags[trader_id] = True
    thread = threading.Thread(target=polling_worker, args=(trader,), daemon=True)
    thread.start()
    active_pollings[trader_id] = thread

    log(f"▶️ Polling multi avviato per trader {trader_id}")
    return {"status": "started", "trader_id": trader_id}

def stop_polling_multi(trader_id: int):
    if not polling_flags.get(trader_id):
        return {"status": "not_running", "message": f"Nessun polling attivo per trader {trader_id}"}

    polling_flags[trader_id] = False
    log(f"⏹️ Richiesta stop polling per trader {trader_id}")
    return {"status": "stopped", "trader_id": trader_id}


# =========================
# check_signal_super adattato al multi
# =========================
def check_signal_super_multi(trader: Trader, symbol: str, base_url_slave: str, previous_signal: str, current_signal: str):
    """
    Versione isolata di check_signal_super per singolo trader.
    Tutte le variabili globali sono passate come parametro.
    """
    logs.clear()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    df = get_data(symbol, TIMEFRAME, N_CANDLES, base_url_slave)
    if df is None:
        return

    ema_short = compute_ema(df, PARAMETERS["EMA_short"])
    ema_long  = compute_ema(df, PARAMETERS["EMA_long"])
    rsi       = compute_rsi(df, PARAMETERS["RSI_period"])

    buy_condition  = ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 65
    sell_condition = ema_short.iloc[-1] < ema_long.iloc[-1] and rsi.iloc[-1] > 35

    positions = []
    try:
        resp = safe_get(f"{base_url_slave}/positions", timeout=10)
        if resp is None:
            log(f"❌ Trader {trader.id} slave offline")
            return
        resp.raise_for_status()
        positions = resp.json()
    except Exception as e:
        log(f"❌ Errore posizioni slave trader {trader.id}: {e}")
        return

    def slave_has_position(order_type=None):
        return any(
            p.get("symbol") == symbol and (order_type is None or p.get("type") == order_type)
            for p in positions
        )

    # =========================
    # BUY
    # =========================
    if buy_condition:
        current_signal = "BUY"
        if not slave_has_position(0) and not slave_has_position(1):
            send_buy_to_slave()
        previous_signal = current_signal
        return

    # =========================
    # SELL
    # =========================
    if sell_condition:
        current_signal = "SELL"
        if not slave_has_position(1) and not slave_has_position(0):
            send_sell_to_slave()
        previous_signal = current_signal
        return

    # =========================
    # HOLD
    # =========================
    current_signal = "HOLD"
    previous_signal = current_signal
    log(f"[{now}] HOLD per trader {trader.id} ({symbol})")

def send_buy_to_slave():

    info_url = f"{BASE_URL_SLAVE}/symbol_info/{SYMBOL}"
    log(f"🔍 Richiedo info simbolo allo slave: {info_url}")
    
    resp = safe_get(info_url, timeout=10)

    sym_info = resp.json()

    # Prendi lo stop loss dal trader, se presente
     # 🔹 2️⃣ Recupero tick dal server slave via API
    tick_url = f"{BASE_URL_SLAVE}/symbol_tick/{SYMBOL}"
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

        if resp.status_code != 200:
            log(f"❌ Errore dallo slave: HTTP {resp.status_code}")
            return False

        return True

    except requests.RequestException as e:
        log(f"❌ Errore invio ordine: {e}")
        return False


def send_sell_to_slave():

    info_url = f"{BASE_URL_SLAVE}/symbol_info/{SYMBOL}"
    log(f"🔍 Richiedo info simbolo allo slave: {info_url}")
    
    resp = safe_get(info_url, timeout=10)
    if resp is None:
        log("❌ Impossibile ottenere symbol_info dallo slave")
        return False

    sym_info = resp.json()

    # 🔹 Tick request
    tick_url = f"{BASE_URL_SLAVE}/symbol_tick/{SYMBOL}"
    log(f"📡 Richiedo tick allo slave: {tick_url}")

    resp_tick = safe_get(tick_url, timeout=10)
    if resp_tick is None or resp_tick.status_code != 200:
        log(f"⚠️ Nessun tick disponibile per {SYMBOL} dallo slave")
        return False

    tick = resp_tick.json()
    if not tick or "bid" not in tick or "ask" not in tick:
        log(f"⚠️ Tick incompleto o non valido per {SYMBOL}: {tick}")
        return False

    log(f"✅ Tick ricevuto per {SYMBOL}: bid={tick['bid']}, ask={tick['ask']}")

    # --- CALCOLO SL / TP PER SELL ---
    sl_pips = CURRENT_TRADER.sl
    tp_pips = CURRENT_TRADER.tp

    pip_value = float(sym_info.get("point"))

    # SL SOPRA IL PREZZO BID (per SELL)
    if sl_pips and float(sl_pips) > 0:
        sl_distance = float(sl_pips) * pip_value
        calculated_sl = tick["bid"] + sl_distance
    else:
        calculated_sl = None

    # TP SOTTO IL PREZZO BID (per SELL)
    if tp_pips and float(tp_pips) > 0:
        tp_distance = float(tp_pips) * pip_value
        calculated_tp = tick["bid"] - tp_distance
    else:
        calculated_tp = None

    sl_value = calculated_sl
    tp_value = calculated_tp
    trader_id = CURRENT_TRADER.id

    log(f"SL = {sl_value}, TP = {tp_value}")

    # Endpoint Manager che invia allo slave
    url = f"{BASE_URL}/db/traders/{trader_id}/open_order_on_slave"

    payload = {
        "trader_id": trader_id,
        "order_type": "sell",
        "volume": 0.10,
        "symbol": SYMBOL,
        "sl": sl_value,
        "tp": tp_value,
    }

    log(f"📤 Invio SELL [symbol={SYMBOL}] allo SLAVE → {url}")

    try:
        resp = requests.post(url, json=payload, timeout=10)
        log(f"📥 Risposta SLAVE: {resp.text}")

        if resp.status_code != 200:
            log(f"❌ Errore dallo slave: HTTP {resp.status_code}")
            return False

        return True

    except requests.RequestException as e:
        log(f"❌ Errore invio ordine SELL: {e}")
        return False
