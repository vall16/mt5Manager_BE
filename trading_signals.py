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
from indicators import (
    compute_ema,
    compute_rsi,
    compute_macd,
    compute_atr,
    compute_bollinger,
    compute_hma,
    compute_adx
)

import pandas as pd
import threading
import time
import requests

router = APIRouter()

load_dotenv()  # legge il file .env

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

# Stato globale del segnale
current_signal = "HOLD"
previous_signal = "HOLD"

# polling_thread = None
polling_running = False

polling_timer = None  # riferimento al Timer

base_url_slave = "http://127.0.0.1:9001"


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

# def compute_ema(df, period):
#     return df['close'].ewm(span=period, adjust=False).mean()

# def compute_rsi(df, period):
#     delta = df['close'].diff()
#     gain = delta.clip(lower=0)
#     loss = -delta.clip(upper=0)
#     avg_gain = gain.rolling(period).mean()
#     avg_loss = loss.rolling(period).mean()
#     rs = avg_gain / avg_loss
#     rsi = 100 - (100 / (1 + rs))
#     return rsi
# aggiunti da poco
# def compute_macd(df, fast=12, slow=26, signal=9):
#     ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
#     ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
#     macd = ema_fast - ema_slow
#     macd_signal = macd.ewm(span=signal, adjust=False).mean()
#     return macd, macd_signal

# def compute_atr(df, period=14):
#     df['tr'] = df['high'] - df['low']
#     atr = df['tr'].rolling(period).mean()
#     return atr
# def compute_bollinger(df, period=20, num_std=2):
#     """
#     Calcola le Bollinger Bands.
    
#     Args:
#         df (DataFrame): dati OHLC con colonna 'close'
#         period (int): periodo della SMA
#         num_std (float): numero di deviazioni standard per le bande

#     Returns:
#         tuple: (SMA, upper_band, lower_band) come pd.Series
#     """
#     sma = df['close'].rolling(window=period).mean()
#     std = df['close'].rolling(window=period).std()

#     upper_band = sma + (std * num_std)
#     lower_band = sma - (std * num_std)

#     return sma, upper_band, lower_band

# import numpy as np

# def compute_hma(df, period=16):
#     """
#     Calcola l'Hull Moving Average (HMA) su una serie 'close'.

#     Args:
#         df (DataFrame): dati OHLC con colonna 'close'
#         period (int): periodo della HMA (tipico: 16, 20, 21)

#     Returns:
#         pd.Series: serie HMA
#     """
#     half_period = int(period / 2)
#     sqrt_period = int(np.sqrt(period))

#     # WMA ponderata
#     def wma(series, n):
#         weights = np.arange(1, n + 1)
#         return series.rolling(n).apply(lambda x: np.dot(x, weights)/weights.sum(), raw=True)

#     wma_half = wma(df['close'], half_period)
#     wma_full = wma(df['close'], period)

#     hma = wma(2 * wma_half - wma_full, sqrt_period)
#     return hma

# def compute_adx(df, period=14):
#     """
#     Calcola l'Average Directional Index (ADX) e i componenti +DI e -DI.

#     Args:
#         df (DataFrame): dati OHLC con colonne 'high', 'low', 'close'
#         period (int): periodo per il calcolo (default=14)

#     Returns:
#         pd.DataFrame: colonne ['ADX', '+DI', '-DI']
#     """
#     high = df['high']
#     low = df['low']
#     close = df['close']

#     # True Range
#     tr1 = high - low
#     tr2 = (high - close.shift()).abs()
#     tr3 = (low - close.shift()).abs()
#     tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

#     # +DM e -DM
#     up_move = high - high.shift()
#     down_move = low.shift() - low
#     plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
#     minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

#     # Smoothed TR, +DM, -DM
#     atr = tr.rolling(period).mean()
#     plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
#     minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr

#     dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
#     adx = dx.rolling(period).mean()

#     return pd.DataFrame({'ADX': adx, '+DI': plus_di, '-DI': minus_di})


signal_lock = threading.Lock()
# gestisce buy e sell
def check_signal():

    logs.clear()

    with signal_lock:
        global current_signal, previous_signal, BASE_URL_SLAVE

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        positions = []

        # ============================
        #   🔄 Recupera info trader/slave
        # ============================
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        trader = get_trader(cursor, CURRENT_TRADER.id)
        if not trader:
            log("❌ Trader non trovato.")
            return

        BASE_URL_SLAVE = f"http://{trader['slave_ip']}:{trader['slave_port']}"

        # if not mt5.initialize():
        #     print("Errore MT5:", mt5.last_error())
        #     return

        df = get_data(SYMBOL, TIMEFRAME, N_CANDLES,BASE_URL_SLAVE)
        if df is None:
            return

        ema_short = compute_ema(df, PARAMETERS["EMA_short"])
        ema_long = compute_ema(df, PARAMETERS["EMA_long"])
        rsi = compute_rsi(df, PARAMETERS["RSI_period"])

        # ============================
        #   🔍 Determinazione segnale
        # ============================
        buy_condition  = ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 68
        sell_condition = ema_short.iloc[-1] < ema_long.iloc[-1] and rsi.iloc[-1] > 32

        
        # ============================
        #   📌 Recupero posizioni SLAVE
        # ============================
        try:
            positions_url = f"{BASE_URL_SLAVE}/positions"
            resp = safe_get(positions_url, timeout=10)

            if resp is None:
                log("❌ Slave offline → esco")
                return

            resp.raise_for_status()
            positions = resp.json()

        except Exception as e:
            log(f"❌ Errore posizioni slave: {e}")
            return

        # helper
        def slave_has_position(symbol):
            return any(p.get("symbol") == symbol for p in positions)

        # ============================
        #   📈 BUY SIGNAL
        # ============================
        # if buy_condition:
        #     current_signal = "BUY"
        #     log("───────S-I-G-N-A-L──────────")
        #     log(f"🔥 [{now}] BUY signal per {SYMBOL}")

        #     # Se lo SLAVE ha già BUY → skip
        #     if slave_has_position(SYMBOL):
        #         log(f"⚠️ BUY su {SYMBOL} già aperto. Skip.")
        #         previous_signal = current_signal
        #         return

        #     # Non aprire BUY se una SELL è aperta (es. hedge vietato)
        #     if any(p.get("type") == 1 and p.get("symbol") == SYMBOL for p in positions):
        #         log(f"⚠️ Esiste SELL aperta su {SYMBOL}, skip BUY.")
        #         previous_signal = current_signal
        #         return

        #     log("🚀 Invio BUY allo SLAVE")
        #     send_buy_to_slave()
        #     previous_signal = current_signal
        #     return

        if buy_condition:
            current_signal = "BUY"
            log("───────S-I-G-N-A-L──────────")
            log(f"🔥 [{now}] BUY signal per {SYMBOL}")

            # Chiudi SELL aperta se esiste (reverse: se arriva il segnale BUY, chiude il SELL)
            if any(p.get("type") == 1 and p.get("symbol") == SYMBOL for p in positions):
                log(f"⚠️ SELL aperta → chiudo SELL prima di aprire BUY")
                close_slave_position()  # <-- chiusura reverse

            # Se lo SLAVE ha già BUY → skip
            if any(p.get("symbol") == SYMBOL and p.get("type") == 0 for p in positions):
                log(f"⚠️ BUY su {SYMBOL} già aperto. Skip.")
                previous_signal = current_signal
                return

            log("🚀 Invio BUY allo SLAVE")
            send_buy_to_slave()
            previous_signal = current_signal
            return

        # ============================
        #   📉 SELL SIGNAL
        # ============================
        # if sell_condition:
        #     current_signal = "SELL"
        #     log("───────S-I-G-N-A-L──────────")
        #     log(f"🔻 [{now}] SELL signal per {SYMBOL}")

        #     # Se lo SLAVE ha già SELL → skip
        #     if any(p.get("symbol") == SYMBOL and p.get("type") == 1 for p in positions):
        #         log(f"⚠️ SELL su {SYMBOL} già aperta. Skip.")
        #         previous_signal = current_signal
        #         return

        #     # Non aprire SELL se un BUY è aperto
        #     if any(p.get("type") == 0 and p.get("symbol") == SYMBOL for p in positions):
        #         log(f"⚠️ BUY aperto su {SYMBOL}, skip SELL.")
        #         previous_signal = current_signal
        #         return

        #     log("🚀 Invio SELL allo SLAVE")
        #     send_sell_to_slave()   
        #     previous_signal = current_signal
        #     return

        if sell_condition:
            current_signal = "SELL"
            log("───────S-I-G-N-A-L──────────")
            log(f"🔻 [{now}] SELL signal per {SYMBOL}")

            # Chiudi BUY aperta se esiste (reverse: se arriva il segnale SELL, chiude il BUY)
            if any(p.get("type") == 0 and p.get("symbol") == SYMBOL for p in positions):
                log(f"⚠️ BUY aperta → chiudo BUY prima di aprire SELL")
                close_slave_position()  # <-- chiusura reverse

            # Se lo SLAVE ha già SELL → skip
            if any(p.get("symbol") == SYMBOL and p.get("type") == 1 for p in positions):
                log(f"⚠️ SELL su {SYMBOL} già aperta. Skip.")
                previous_signal = current_signal
                return

            log("🚀 Invio SELL allo SLAVE")
            send_sell_to_slave()
            previous_signal = current_signal
            return


        # ============================
        #   ⏸️ HOLD (nessun BUY / SELL)
        # ============================
        current_signal = "HOLD"
        log("───────S-I-G-N-A-L──────────")
        log(f"⚠️  [{now}] HOLD per {SYMBOL}")

        log(f"🔄 previous_signal={previous_signal}, current={current_signal}")

        # Se era BUY e diventa HOLD → chiudi BUY
        if previous_signal == "BUY":
            log(f"⚠️ BUY → HOLD: chiudo BUY {SYMBOL}")
            # provo a togliere chiusura per cambio..
            # close_slave_position()

        # Se era SELL e diventa HOLD → chiudi SELL
        if previous_signal == "SELL":
            log(f"⚠️ SELL → HOLD: chiudo SELL {SYMBOL}")
            # provo a togliere chiusura per cambio..
            # close_slave_position()

        previous_signal = current_signal

# 22/12: ottimizzato per XAUUSD con molte migliorie
signal_lock = threading.Lock()
# def è check_trendguard_xau_signal()!!:
def check_trendguard_xau_signal():

    logs.clear()

    with signal_lock:
        global current_signal, previous_signal, BASE_URL_SLAVE

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        positions = []

        # ============================
        # 🔄 Recupero info trader / slave
        # ============================
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        trader = get_trader(cursor, CURRENT_TRADER.id)
        if not trader:
            log("❌ Trader non trovato.")
            return

        BASE_URL_SLAVE = f"http://{trader['slave_ip']}:{trader['slave_port']}"

        # ============================
        # 📊 Dati di mercato
        # ============================
        df = get_data(SYMBOL, TIMEFRAME, N_CANDLES, BASE_URL_SLAVE)
        if df is None or len(df) < 50:
            log("❌ Dati insufficienti")
            return

        ema_short = compute_ema(df, PARAMETERS["EMA_short"])
        ema_long  = compute_ema(df, PARAMETERS["EMA_long"])
        rsi       = compute_rsi(df, PARAMETERS["RSI_period"])
        # atr       = compute_atr(df, PARAMETERS["ATR_period"])
        atr = compute_atr(df, PARAMETERS.get("ATR_period", 14))


        ema_s = ema_short.iloc[-1]
        ema_l = ema_long.iloc[-1]
        rsi_v = rsi.iloc[-1]
        atr_v = atr.iloc[-1]

        # ============================
        # ⚙️ Parametri XAUUSD (tuning)
        # ============================
        RSI_BUY_MAX  = PARAMETERS.get("RSI_BUY_MAX", 62)
        RSI_SELL_MIN = PARAMETERS.get("RSI_SELL_MIN", 38)
        ATR_MIN      = PARAMETERS.get("ATR_MIN", atr.mean() * 0.7)

        # ============================
        # 🌪️ Filtro volatilità
        # ============================
        if atr_v < ATR_MIN:
            log(f"⏸️ [{now}] ATR troppo basso ({atr_v:.2f}) → HOLD")
            current_signal = "HOLD"
            previous_signal = current_signal
            return

        # ============================
        # 🔍 Condizioni di trend
        # ============================
        trend_up   = ema_s > ema_l
        trend_down = ema_s < ema_l

        buy_condition  = trend_up   and rsi_v < RSI_BUY_MAX
        sell_condition = trend_down and rsi_v > RSI_SELL_MIN

        # ============================
        # 📌 Recupero posizioni SLAVE
        # ============================
        try:
            resp = safe_get(f"{BASE_URL_SLAVE}/positions", timeout=10)
            if resp is None:
                log("❌ Slave offline")
                return

            resp.raise_for_status()
            positions = resp.json()

        except Exception as e:
            log(f"❌ Errore posizioni slave: {e}")
            return

        def has_buy():
            return any(p["symbol"] == SYMBOL and p["type"] == 0 for p in positions)

        def has_sell():
            return any(p["symbol"] == SYMBOL and p["type"] == 1 for p in positions)

        # ============================
        # 📈 BUY SIGNAL
        # ============================
        if buy_condition:
            current_signal = "BUY"
            log("─────── T R E N D G U A R D ───────")
            log(f"🔥 [{now}] BUY XAU | RSI={rsi_v:.1f} ATR={atr_v:.2f}")

            if has_buy():
                log("⚠️ BUY già aperto → skip")
                previous_signal = current_signal
                return

            if has_sell():
                log("⚠️ SELL aperta → hedge vietato")
                previous_signal = current_signal
                return

            send_buy_to_slave()
            previous_signal = current_signal
            return

        # ============================
        # 📉 SELL SIGNAL
        # ============================
        if sell_condition:
            current_signal = "SELL"
            log("─────── T R E N D G U A R D ───────")
            log(f"🔻 [{now}] SELL XAU | RSI={rsi_v:.1f} ATR={atr_v:.2f}")

            if has_sell():
                log("⚠️ SELL già aperta → skip")
                previous_signal = current_signal
                return

            if has_buy():
                log("⚠️ BUY aperto → hedge vietato")
                previous_signal = current_signal
                return

            send_sell_to_slave()
            previous_signal = current_signal
            return

        # ============================
        # ⏸️ HOLD INTELLIGENTE
        # ============================
        current_signal = "HOLD"
        log("─────── T R E N D G U A R D ───────")
        log(f"⏸️ [{now}] HOLD | RSI={rsi_v:.1f}")

        # ❗ NON chiudere se il trend è ancora valido
        if previous_signal == "BUY" and trend_up:
            log("🟢 Trend BUY ancora valido → mantengo posizione")
            return

        if previous_signal == "SELL" and trend_down:
            log("🔴 Trend SELL ancora valido → mantengo posizione")
            return

        # 🔥 Qui il trend è davvero rotto → chiudo
        if previous_signal in ("BUY", "SELL"):
            log(f"❌ Trend rotto → chiudo {previous_signal}")
            close_slave_position()

        previous_signal = current_signal


signal_lock = threading.Lock()



# Polling in background
def polling_loop():
    log("▶️ Polling loop partito")

    while True:
        check_signal()
        time.sleep(CHECK_INTERVAL)


@router.post("/start_polling")
def start_polling(trader:Trader):
    
    global polling_running, polling_timer,CURRENT_TRADER, CHECK_INTERVAL, SYMBOL,BASE_URL_SLAVE,CHOSEN_TRADESIGNAL


    # print(">>> start_polling CHIAMATO, trader =", trader)

    # Log del JSON formattato
    trader_json = trader.dict()  # converte in dict
    print(">>> start_polling CHIAMATO, trader JSON:\n", json.dumps(trader_json, indent=2))
    # log(f"📥 Body JSON ricevuto:\n{json.dumps(trader_json, indent=2)}")


    # Salva il trader globale
    CURRENT_TRADER = trader

    try:
        CHECK_INTERVAL = int(trader.customSignalInterval)
    except:
        CHECK_INTERVAL = 2


    log(CHECK_INTERVAL)

    # Imposta simbolo dinamico
    SYMBOL = trader.selectedSymbol

    with signal_lock:
        CHOSEN_TRADESIGNAL = trader.selectedSignal or "BASE"

    log(f"📡 Segnale attivo: {CHOSEN_TRADESIGNAL}")


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



# Endpoint per il frontend
@router.get("/signal")
def get_signal():
    return {"signal": current_signal}

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




def close_slave_position():
    url = f"{BASE_URL}/db/traders/{TRADER_ID}/close_order_on_slave"
    payload = {"symbol": SYMBOL,"trader_id": TRADER_ID}
    log(f"📤 Invio richiesta chiusura posizione [symbol={SYMBOL}] allo SLAVE → {url}")

    try:
        resp = requests.post(url, json=payload, timeout=10)
        log(f"📥 Risposta chiusura SLAVE: {resp.text}")
    except requests.RequestException as e:
        log(f"❌ Errore chiusura posizione: {e}")

import numpy as np
from datetime import datetime

def check_signal_super():
    """
    🔹 Versione avanzata del segnale:
    - Filtri trend e volatilità
    - Controllo posizioni slave
    - Evita falsi segnali in news, gap, low volume
    """
    logs.clear()

    with signal_lock:
        global current_signal, previous_signal, BASE_URL_SLAVE

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        positions = []

        # if not mt5.initialize():
        #     log(f"❌ Errore MT5: {mt5.last_error()}")
        #     return

        df = get_data(SYMBOL, TIMEFRAME, N_CANDLES,BASE_URL_SLAVE)
        if df is None:
            return

        # ------------------------
        # Indicatori principali
        # ------------------------
        ema_short = compute_ema(df, PARAMETERS["EMA_short"])
        ema_long  = compute_ema(df, PARAMETERS["EMA_long"])
        rsi       = compute_rsi(df, PARAMETERS["RSI_period"])
        macd, macd_signal = compute_macd(df)
        atr       = compute_atr(df)
        sma, upper_bb, lower_bb = compute_bollinger(df)
        hma       = compute_hma(df)
        adx_val   = compute_adx(df).iloc[-1]

        # ------------------------
        # Filtri aggiuntivi
        # ------------------------
        # Trend M15
        df15 = get_data(SYMBOL, 15, N_CANDLES,BASE_URL_SLAVE)
        big_trend_up = compute_ema(df15, 20).iloc[-1] > compute_ema(df15, 50).iloc[-1] if df15 is not None else True
        big_trend_down = not big_trend_up

        # Volatilità e volume
        vol_mean = df['tick_volume'].rolling(20).mean().iloc[-1]
        vol_now  = df['tick_volume'].iloc[-1]
        volume_ok = vol_now > vol_mean * 1.3 if vol_mean else True

        volatility_ok = atr.iloc[-1] > atr.iloc[-5] if len(atr) >= 5 else True
        strong_trend = adx_val > 20

        # Gap detection
        gap = abs(df['open'].iloc[-1] - df['close'].iloc[-2]) > (df['close'].iloc[-2]*0.001)
        if gap:
            log("⚠️ GAP troppo grande → Skip")
            current_signal = "HOLD"
            return

        # News filter
        # if is_news_time():
        #     log("⚠️ NEWS event → Skip trading")
        #     current_signal = "HOLD"
        #     return

        # ------------------------
        # Recupero trader / slave
        # ------------------------
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        trader = get_trader(cursor, CURRENT_TRADER.id)
        if not trader:
            log("❌ Trader non trovato")
            return
        BASE_URL_SLAVE = f"http://{trader['slave_ip']}:{trader['slave_port']}"

        try:
            resp = safe_get(f"{BASE_URL_SLAVE}/positions", timeout=10)
            if resp is None:
                log("❌ Slave offline → Skip")
                return
            resp.raise_for_status()
            positions = resp.json()
        except Exception as e:
            log(f"❌ Errore posizioni slave: {e}")
            return

        def slave_has_position(symbol, order_type=None):
            return any(
                p.get("symbol") == symbol and (order_type is None or p.get("type") == order_type)
                for p in positions
            )

        # ------------------------
        # Condizioni BUY
        # ------------------------
        
        try:
            # Indicatori principali (float)
            v_ema_s  = float(ema_short.iloc[-1])
            v_ema_l  = float(ema_long.iloc[-1])
            v_rsi    = float(rsi.iloc[-1])
            v_macd_v = float(macd.iloc[-1])
            v_macd_s = float(macd_signal.iloc[-1])
            v_hma_0  = float(hma.iloc[-1])
            v_hma_1  = float(hma.iloc[-2])

            # Filtri (cast a bool per sicurezza)
            f_vol      = bool(volume_ok)
            f_vola     = bool(volatility_ok)
            f_strong   = bool(strong_trend)
            f_big_up   = bool(big_trend_up)
            f_big_down = bool(big_trend_down)

        except Exception as e:
            log(f"❌ Errore conversione indicatori: {e}")
            current_signal = "HOLD"
            return

        # ---------------------------------------------------------
        # 📈 LOGICA BUY (SUPER-SIGNAL)
        # ---------------------------------------------------------
        buy_condition = (
            v_ema_s > v_ema_l and
            v_rsi < 65 and
            v_macd_v > v_macd_s and
            v_hma_0 > v_hma_1 and
            f_vol and f_vola and f_strong and f_big_up
        )



        # ------------------------
        # Condizioni SELL
        # ------------------------
        
        # 1. Estrazione e validazione dati (Scalari)
        try:
            v_ema_s  = float(ema_short.iloc[-1])
            v_ema_l  = float(ema_long.iloc[-1])
            v_rsi    = float(rsi.iloc[-1])
            v_macd_v = float(macd.iloc[-1])
            v_macd_s = float(macd_signal.iloc[-1])
            v_hma_0  = float(hma.iloc[-1])
            v_hma_1  = float(hma.iloc[-2])

            # Cast esplicito dei filtri per evitare l'errore "truth value of a Series"
            f_vol      = bool(volume_ok)
            f_vola     = bool(volatility_ok)
            f_strong   = bool(strong_trend)
            f_big_down = bool(big_trend_down)
            
        except Exception as e:
            log(f"❌ Errore indicatori SELL: {e}")
            current_signal = "HOLD"
            return

        # 2. Definizione della condizione SELL
        sell_condition = (
            v_ema_s < v_ema_l and
            v_rsi > 35 and
            v_macd_v < v_macd_s and
            v_hma_0 < v_hma_1 and
            f_vol and f_vola and f_strong and f_big_down
        )



        # ============================
        # BUY
        # ============================
        if buy_condition:
            current_signal = "BUY"
            log("───────S-I-G-N-A-L──────────")
            log(f"🔥 [{now}] BUY signal per {SYMBOL}")

            if slave_has_position(SYMBOL):
                log(f"⚠️ BUY su {SYMBOL} già aperto. Skip.")
                previous_signal = current_signal
                return

            if any(p.get("type") == 1 and p.get("symbol") == SYMBOL for p in positions):
                log(f"⚠️ Esiste SELL aperta su {SYMBOL}, skip BUY.")
                previous_signal = current_signal
                return

            log("🚀 Invio BUY allo SLAVE")
            send_buy_to_slave()
            previous_signal = current_signal
            return

        # ============================
        # SELL
        # ============================
        if sell_condition:
            current_signal = "SELL"
            log("───────S-I-G-N-A-L──────────")
            log(f"🔻 [{now}] SELL signal per {SYMBOL}")

            if slave_has_position(SYMBOL, order_type=1):
                log(f"⚠️ SELL su {SYMBOL} già aperta. Skip.")
                previous_signal = current_signal
                return

            if any(p.get("type") == 0 and p.get("symbol") == SYMBOL for p in positions):
                log(f"⚠️ BUY aperto su {SYMBOL}, skip SELL.")
                previous_signal = current_signal
                return

            log("🚀 Invio SELL allo SLAVE")
            send_sell_to_slave()
            previous_signal = current_signal
            return

        # ============================
        # HOLD
        # ============================
        current_signal = "HOLD"
        log("───────S-I-G-N-A-L──────────")
        log(f"⚠️  [{now}] HOLD per {SYMBOL}")
        log(f"🔄 previous_signal={previous_signal}, current_signal={current_signal}")

        # chiudi eventuali posizioni aperte se segnale HOLD
        if previous_signal == "BUY":
            log(f"⚠️ BUY → HOLD: chiudo BUY {SYMBOL}")
            close_slave_position()
        if previous_signal == "SELL":
            log(f"⚠️ SELL → HOLD: chiudo SELL {SYMBOL}")
            close_slave_position()

        previous_signal = current_signal

SIGNAL_HANDLERS = {
    "BASE": check_signal,
    "SUPER": check_signal_super,
    "TRENDGUARD_XAU": check_trendguard_xau_signal,
}

def polling_loop_timer():
    global polling_timer, polling_running,CHOSEN_TRADESIGNAL,CHECK_INTERVAL

    if not polling_running:
        return

    # Forza fallback se vuoto
    if not CHOSEN_TRADESIGNAL:
        log(f"⚠️ CHOSEN_TRADESIGNAL vuoto, forzo fallback a BASE")
        CHOSEN_TRADESIGNAL = "BASE"

    log(f"⏱️ Polling loop → CHOSEN_TRADESIGNAL = '{CHOSEN_TRADESIGNAL}'")

    handler = SIGNAL_HANDLERS.get(CHOSEN_TRADESIGNAL)
    if not handler:
        log(f"❌ Segnale non valido: {CHOSEN_TRADESIGNAL}, fallback BASE")
        handler = check_signal

    handler()

    polling_timer = threading.Timer(CHECK_INTERVAL, polling_loop_timer)
    polling_timer.daemon = True
    polling_timer.start()
