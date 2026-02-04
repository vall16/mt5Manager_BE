# --- STATO MULTI-SESSIONE ---
from datetime import datetime
import os
import threading

from pydantic import BaseModel
from logger import log, logs
from dotenv import load_dotenv
from fastapi import APIRouter
from db import get_trader, get_connection
from models import (
    Trader
)
from indicators.ta import (
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

sessions = {}
sessions_lock = threading.Lock()

router = APIRouter()

load_dotenv()  # legge il file .env

HOST = os.getenv("API_HOST")  # default localhost
PORT = int(os.getenv("API_PORT"))    # default 8080

BASE_URL = f"http://{HOST}:{PORT}"         # costruisce automaticamente l'URL

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


# Quando dal tuo Frontend chiami this.http.post(..., trader), il backend riceve l'oggetto con il suo ID (es. Trader ID 1). Crea la sessione 1. Se dopo un secondo clicchi sul Trader ID 2, il backend crea la sessione 2 senza toccare la 1.
# Ogni trader avrà la sua copia privata di parametri e stato del segnale.
def polling_loop_timer(trader_id):
    """Ciclo di polling specifico per ogni trader"""
    with sessions_lock:
        if trader_id not in sessions:
            return # Il polling è stato fermato
        
        session = sessions[trader_id]
        trader = session["trader"]
        
    # Esegue l'analisi passandogli i dati specifici di QUESTO trader
    run_signal_logic(trader_id)

    # Re-schedula il prossimo controllo basandosi sul customSignalInterval del trader
    interval = int(trader.custom_signal_interval or 5)
    
    with sessions_lock:
        if trader_id in sessions:
            t = threading.Timer(interval, polling_loop_timer, args=[trader_id])
            sessions[trader_id]["timer"] = t
            t.start()


def run_signal_logic(trader_id):
    with sessions_lock:
        if trader_id not in sessions: return
        trader = sessions[trader_id]["trader"]
    
    # Leggiamo quale segnale ha scelto l'utente nel FE
    chosen_signal = trader.selected_signal or "BASE"

    if chosen_signal == "SUPER":
        check_signal_super(trader_id)
    elif chosen_signal == "TRENDGUARD":
        # check_trendguard_xau_signal(trader_id)
        check_signal(trader_id)
    elif chosen_signal == "BASE_NOHOLD":
        
        check_signal_nohold(trader_id)
    else:
        # Il tuo check_signal originale di base
        check_signal(trader_id)

# FA PARTIRE IL POLLING !!
@router.post("/start_polling")
def start_polling(trader: Trader):
    global sessions
    tid = trader.id

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    trader_data = get_trader(cursor, tid)
    if not trader_data:
        log("❌ Trader non trovato nel DB.")
        return {"status": "error"}

    log("📋 Trader ricevuto:")
    for k, v in trader.model_dump().items():
        log(f"   - {k}: {v}")
    
    with sessions_lock:
        if tid in sessions and sessions[tid]["timer"]:
            sessions[tid]["timer"].cancel()

        # Salviamo sia l'oggetto Pydantic sia i dati DB fissi
        sessions[tid] = {
            "trader": trader,
            "trader_data": trader_data,  # <--- qui
            "prev_signal": "HOLD",
            "timer": None
        }

    polling_loop_timer(tid)
    
    log(f"🚀 Trading avviato per Trader {tid} ({trader.name}) su {trader.selected_symbol}")
    return {"status": "started", "trader_id": tid}


class StopPollingRequest(BaseModel):
    trader_id: int

# STOPPA IL POLLING !!
@router.post("/stop_polling")
def stop_polling(req: StopPollingRequest):
    trader_id = req.trader_id
    with sessions_lock:
        if trader_id in sessions:
            timer = sessions[trader_id].get("timer")
            if timer:
                timer.cancel()
            del sessions[trader_id]
            log(f"⏹️ Polling fermato manualmente per Trader {trader_id}")
            return {"status": "stopped", "trader_id": trader_id}

    return {"status": "not_running", "message": f"Trader {trader_id} non attivo"}

# chiude ad ogni cambio di segnale (!)
def check_signal(trader_id):

    logs.clear()
    # session
    with sessions_lock:
        if trader_id not in sessions: return
        session = sessions[trader_id]
        trader = session["trader"]
        trader_data = session["trader_data"]  # <--- qui
        prev_signal = session["prev_signal"]

    slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"

    # variabili locali
    symbol = session["trader"].selected_symbol  # da Pydantic
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Parametri indicatori (usiamo quelli nel trader o default)
    params = {"EMA_short": 5, "EMA_long": 15, "RSI_period": 14} 

    # 3. Download dati dal suo slave specifico
    df = get_data(symbol, 5, 50, slave_url) # Timeframe 5, 50 candele
    if df is None: return

    # 4. Calcolo Indicatori
    ema_short = compute_ema(df, params["EMA_short"])
    ema_long = compute_ema(df, params["EMA_long"])
    rsi = compute_rsi(df, params["RSI_period"])

    buy_cond = ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 68
    sell_cond = ema_short.iloc[-1] < ema_long.iloc[-1] and rsi.iloc[-1] > 32

    # 5. Controllo posizioni attive sullo slave
    positions = []
    try:
        resp = requests.get(f"{slave_url}/positions", timeout=5)
        if resp.status_code == 200:
            positions = resp.json()
    except:
        log(f"❌ Trader {trader_id}: Slave non raggiungibile")
        return

    has_buy = any(p["symbol"] == symbol and p["type"] == 0 for p in positions)
    has_sell = any(p["symbol"] == symbol and p["type"] == 1 for p in positions)

    # 6. Logica Decisionale
    new_signal = "HOLD"
    

    if buy_cond:
        new_signal = "BUY"
        log("───────S-I-G-N-A-L──────────")
        log(f"🔥 [{now}] BUY signal per {symbol}")
        if not has_buy:
            if has_sell: close_slave_position(trader_id) # Reverse
            send_buy_to_slave(trader_id)
            log(f"🔥 Trader {trader_id}: Segnale BUY inviato")

    elif sell_cond:
        new_signal = "SELL"
        log("───────S-I-G-N-A-L──────────")
        log(f"🔥 [{now}] SELL signal per {symbol}")
        if not has_sell:
            if has_buy: close_slave_position(trader_id) # Reverse
            send_sell_to_slave(trader_id)
            log(f"🔻 Trader {trader_id}: Segnale SELL inviato")

    else:
        # HOLD: Se vuoi chiusura immediata (come discusso prima, valuta se tenerlo)
        log("───────S-I-G-N-A-L──────────")
        log(f"🔥 [{now}] HOLD signal per {symbol}")

        if prev_signal == "BUY" and has_buy:
             close_slave_position(trader_id)
        elif prev_signal == "SELL" and has_sell:
             close_slave_position(trader_id)

    # 7. Aggiornamento Segnale Precedente nella sessione corretta
    with sessions_lock:
        if trader_id in sessions:
            sessions[trader_id]["prev_signal"] = new_signal

# //chiude solo buy --> sell e sell --> buy
def check_signal_nohold(trader_id):

    logs.clear()
    # session
    with sessions_lock:
        if trader_id not in sessions: return
        session = sessions[trader_id]
        trader = session["trader"]
        trader_data = session["trader_data"]  # <--- qui
        prev_signal = session["prev_signal"]

    slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"

    # variabili locali
    symbol = session["trader"].selected_symbol  # da Pydantic
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Leggiamo quale segnale ha scelto l'utente nel FE
    chosen_signal = trader.selected_signal or "BASE"
    
    # Parametri indicatori (usiamo quelli nel trader o default)
    params = {"EMA_short": 5, "EMA_long": 15, "RSI_period": 14} 

    # 3. Download dati dal suo slave specifico
    df = get_data(symbol, 5, 50, slave_url) # Timeframe 5 min, 50 candele
    if df is None: return

    # 4. Calcolo Indicatori
    ema_short = compute_ema(df, params["EMA_short"])
    ema_long = compute_ema(df, params["EMA_long"])
    rsi = compute_rsi(df, params["RSI_period"])

    buy_cond = ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 68
    sell_cond = ema_short.iloc[-1] < ema_long.iloc[-1] and rsi.iloc[-1] > 32

    # 5. Controllo posizioni attive sullo slave
    positions = []
    try:
        resp = requests.get(f"{slave_url}/positions", timeout=5)
        if resp.status_code == 200:
            positions = resp.json()
    except:
        log(f"❌ Trader {trader_id}: Slave non raggiungibile")
        return

    has_buy = any(p["symbol"] == symbol and p["type"] == 0 for p in positions)
    has_sell = any(p["symbol"] == symbol and p["type"] == 1 for p in positions)

    # 6. Logica Decisionale
    new_signal = "HOLD"
    

    if buy_cond:
        new_signal = "BUY"
        # log("───────S-I-G-N-A-L────────── ")
        log(f"─────── S-I-G-N-A-L [{chosen_signal}] ──────────")

        log(f"🔥 [{now}] BUY signal per {symbol}")
        if not has_buy:
            if has_sell: close_slave_position(trader_id) # Reverse
            send_buy_to_slave(trader_id)
            log(f"🔥 Trader {trader_id}: Segnale BUY inviato")

    elif sell_cond:
        new_signal = "SELL"
        # log("───────S-I-G-N-A-L────────── ")
        log(f"─────── S-I-G-N-A-L [{chosen_signal}] ──────────")

        log(f"🔥 [{now}] SELL signal per {symbol}")
        if not has_sell:
            if has_buy: close_slave_position(trader_id) # Reverse
            send_sell_to_slave(trader_id)
            log(f"🔻 Trader {trader_id}: Segnale SELL inviato")

    else:
        # HOLD: Se vuoi chiusura immediata (come discusso prima, valuta se tenerlo)
        # log("───────S-I-G-N-A-L────────── ")
        log(f"─────── S-I-G-N-A-L [{chosen_signal}] ──────────")

        log(f"🔥 [{now}] HOLD signal per {symbol}")

        # CON HOLD NON CHIUDE !
        # if prev_signal == "BUY" and has_buy:
        #     #  close_slave_position(trader_id)
        # elif prev_signal == "SELL" and has_sell:
        #     #  close_slave_position(trader_id)

    # 7. Aggiornamento Segnale Precedente nella sessione corretta
    with sessions_lock:
        if trader_id in sessions:
            sessions[trader_id]["prev_signal"] = new_signal


def check_signal_super(trader_id):
    """
    🔹 Versione SUPER per singolo trader
    - Indicatori avanzati
    - Filtri trend / volatilità
    - Gestione sessione per-trader
    """

    logs.clear()

    # =========================
    # 1. Recupero sessione
    # =========================
    # session
    # with sessions_lock:
    #     if trader_id not in sessions: return
    #     session = sessions[trader_id]
    #     trader = session["trader"]
    #     trader_data = session["trader_data"]  # <--- qui
    #     prev_signal = session["prev_signal"]

    # slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"

        # session
    with sessions_lock:
        if trader_id not in sessions: return
        session = sessions[trader_id]
        trader = session["trader"]
        trader_data = session["trader_data"]  # <--- qui
        prev_signal = session["prev_signal"]

    slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"

    symbol = session["trader"].selected_symbol  # da Pydantic
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # =========================
    # 2. Parametri indicatori
    # =========================
    params = {
        "EMA_short": 5,
        "EMA_long": 15,
        "RSI_period": 14
    }

    # =========================
    # 3. Dati mercato
    # =========================
    df = get_data(symbol, 5, 50, slave_url)
    if df is None or df.empty:
        return

    # =========================
    # 4. Indicatori
    # =========================
    ema_short = compute_ema(df, params["EMA_short"])
    ema_long  = compute_ema(df, params["EMA_long"])
    rsi       = compute_rsi(df, params["RSI_period"])
    macd, macd_signal = compute_macd(df)
    atr       = compute_atr(df)
    hma       = compute_hma(df)

    adx_df = compute_adx(df)
    v_adx = float(adx_df["ADX"].iloc[-1])

    # =========================
    # 5. Trend superiore (M15)
    # =========================
    df15 = get_data(symbol, 15, 50, slave_url)
    big_trend_up = (
        compute_ema(df15, 20).iloc[-1] >
        compute_ema(df15, 50).iloc[-1]
    ) if df15 is not None else True

    big_trend_down = not big_trend_up

    # =========================
    # 6. Filtri volume / volatilità
    # =========================
    v_mean = float(df["tick_volume"].rolling(20).mean().iloc[-1])
    v_now  = float(df["tick_volume"].iloc[-1])

    f_volume_ok     = v_now > (v_mean * 1.3) if v_mean > 0 else True
    f_volatility_ok = float(atr.iloc[-1]) > float(atr.iloc[-5]) if len(atr) >= 5 else True
    f_strong_trend  = v_adx > 20

    # =========================
    # 7. GAP filter
    # =========================
    gap = abs(df["open"].iloc[-1] - df["close"].iloc[-2]) > (df["close"].iloc[-2] * 0.001)
    if gap:
        log(f"⚠️ Trader {trader_id}: GAP → HOLD")
        return

    # =========================
    # 8. Posizioni slave
    # =========================
    try:
        resp = requests.get(f"{slave_url}/positions", timeout=5)
        if resp.status_code != 200:
            return
        positions = resp.json()
    except:
        log(f"❌ Trader {trader_id}: Slave non raggiungibile")
        return

    has_buy  = any(p["symbol"] == symbol and p["type"] == 0 for p in positions)
    has_sell = any(p["symbol"] == symbol and p["type"] == 1 for p in positions)

    # =========================
    # 9. Condizioni CORE
    # =========================
    core_buy = (
        ema_short.iloc[-1] > ema_long.iloc[-1] and
        macd.iloc[-1] > macd_signal.iloc[-1] and
        hma.iloc[-1] > hma.iloc[-2] and
        rsi.iloc[-1] < 68
    )

    core_sell = (
        ema_short.iloc[-1] < ema_long.iloc[-1] and
        macd.iloc[-1] < macd_signal.iloc[-1] and
        hma.iloc[-1] < hma.iloc[-2] and
        rsi.iloc[-1] > 32
    )

    conf_buy  = sum([big_trend_up, f_volume_ok, f_strong_trend]) >= 1
    conf_sell = sum([big_trend_down, f_volume_ok, f_strong_trend]) >= 1

    buy_cond  = core_buy  and conf_buy
    sell_cond = core_sell and conf_sell

    # =========================
    # 10. Decisione
    # =========================
    new_signal = "HOLD"

    if buy_cond:
        new_signal = "BUY"
        if not has_buy:
            if has_sell:
                close_slave_position(trader_id)
            send_buy_to_slave(trader_id)
            log(f"🔥 Trader {trader_id}: BUY inviato")

    elif sell_cond:
        new_signal = "SELL"
        if not has_sell:
            if has_buy:
                close_slave_position(trader_id)
            send_sell_to_slave(trader_id)
            log(f"🔻 Trader {trader_id}: SELL inviato")

    else:
        # hold --> buy e sell --> buy non chiude...
        # if prev_signal == "BUY" and has_buy:
        #     # close_slave_position(trader_id)
        # elif prev_signal == "SELL" and has_sell:
        #     # close_slave_position(trader_id)
        log("───────S-I-G-N-A-L──────────")
        log(f"🔥 [{now}] HOLD signal per {symbol}")

    # =========================
    # 11. Update sessione
    # =========================
    with sessions_lock:
        if trader_id in sessions:
            sessions[trader_id]["prev_signal"] = new_signal


def send_buy_to_slave(trader_id):

    with sessions_lock:
        if trader_id not in sessions: 
            return
        session = sessions[trader_id]
        trader = session["trader"]
        trader_data = session["trader_data"]  # <-- dati completi DB già presi nello start_polling

    symbol = trader.selected_symbol
    slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"

    manager_url = os.getenv("MANAGER_URL") # Il tuo BASE_URL del manager

    # 1. Recupero info simbolo e tick dallo slave specifico
    try:
        info_resp = requests.get(f"{slave_url}/symbol_info/{symbol}", timeout=10)
        tick_resp = requests.get(f"{slave_url}/symbol_tick/{symbol}", timeout=10)
        
        if info_resp.status_code != 200 or tick_resp.status_code != 200:
            log(f"❌ Trader {trader_id}: Impossibile recuperare dati dallo slave")
            return

        sym_info = info_resp.json()
        tick = tick_resp.json()
    except Exception as e:
        log(f"❌ Trader {trader_id}: Errore connessione slave: {e}")
        return

    # 2. Calcolo SL e TP dinamici
    pip_value = float(sym_info.get("point", 0.00001))
    sl_value = tick["ask"] - (float(trader.sl) * pip_value) if trader.sl > 0 else None
    tp_value = tick["ask"] + (float(trader.tp) * pip_value) if trader.tp > 0 else None

    volume = trader.fix_lot

    # 3. Invio ordine tramite l'endpoint del Manager che comunica con lo Slave
    url = f"{BASE_URL}/db/traders/{trader_id}/open_order_on_slave"
    payload = {
        "trader_id": trader_id,
        "order_type": "buy",
        # "volume": 0.10, # O trader.defaultVolume se lo hai
        "volume": volume, 
        "symbol": symbol,
        "sl": sl_value,
        "tp": tp_value,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        log(f"📥 Trader {trader_id} BUY Response: {resp.text}")
    except Exception as e:
        log(f"❌ Trader {trader_id}: Errore invio ordine: {e}")

def close_slave_position(trader_id):
    with sessions_lock:
        if trader_id not in sessions: return
        trader = sessions[trader_id]["trader"]

    manager_url = os.getenv("MANAGER_URL")
    url = f"{BASE_URL}/db/traders/{trader_id}/close_order_on_slave"
    payload = {
        "symbol": trader.selected_symbol,
        "trader_id": trader_id
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        log(f"📥 Trader {trader_id} CLOSE Response: {resp.text}")
    except Exception as e:
        log(f"❌ Trader {trader_id}: Errore chiusura: {e}")


def send_sell_to_slave(trader_id):
    with sessions_lock:
        if trader_id not in sessions:
            return
        session = sessions[trader_id]
        trader = session["trader"]
        trader_data = session["trader_data"]  # <-- dati DB master/slave

    symbol = trader.selected_symbol
    slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"

    manager_url = os.getenv("MANAGER_URL")

    # 1️⃣ Recupero info simbolo e tick dallo slave
    try:
        info_resp = requests.get(f"{slave_url}/symbol_info/{symbol}", timeout=10)
        tick_resp = requests.get(f"{slave_url}/symbol_tick/{symbol}", timeout=10)

        if info_resp.status_code != 200 or tick_resp.status_code != 200:
            log(f"❌ Trader {trader_id}: Impossibile recuperare dati dallo slave")
            return

        sym_info = info_resp.json()
        tick = tick_resp.json()

        if not tick or "bid" not in tick or "ask" not in tick:
            log(f"⚠️ Trader {trader_id}: Tick incompleto o non valido per {symbol}")
            return

    except Exception as e:
        log(f"❌ Trader {trader_id}: Errore connessione slave: {e}")
        return

    # 2️⃣ Calcolo SL e TP dinamici (SELL)
    pip_value = float(sym_info.get("point", 0.00001))
    sl_value = tick["bid"] + (float(trader.sl) * pip_value) if trader.sl > 0 else None
    tp_value = tick["bid"] - (float(trader.tp) * pip_value) if trader.tp > 0 else None

    log(f"Trader {trader_id} SELL: SL={sl_value}, TP={tp_value}")

    volume = trader.fix_lot

    # 3️⃣ Invio ordine tramite Manager
    url = f"{BASE_URL}/db/traders/{trader_id}/open_order_on_slave"
    payload = {
        "trader_id": trader_id,
        "order_type": "sell",
        # "volume": 0.10,  # o trader.defaultVolume se lo hai
        "volume": volume,
        "symbol": symbol,
        "sl": sl_value,
        "tp": tp_value,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        log(f"📥 Trader {trader_id} SELL Response: {resp.text}")
    except Exception as e:
        log(f"❌ Trader {trader_id}: Errore invio ordine SELL: {e}")
