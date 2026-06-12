# --- STATO MULTI-SESSIONE (REFACTORED: STRATEGY PATTERN) ---
from datetime import datetime
import os
import threading
import time

from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi import APIRouter
from db import get_trader, get_connection
from models import Trader
from indicators.ta import (
    compute_ema, compute_rsi, compute_macd, compute_atr,
    compute_bollinger, compute_hma, compute_adx
)
import pandas as pd
import requests

# ─────────────────────── GLOBAL STATE ───────────────────────

sessions = {}
sessions_lock = threading.Lock()
router = APIRouter()

load_dotenv()
HOST = os.getenv("API_HOST", "localhost")
PORT = int(os.getenv("API_PORT", 8080))
BASE_URL = f"http://{HOST}:{PORT}"


# ─────────────────────── HELPERS ───────────────────────

def get_data(symbol, timeframe, n_candles, agent_url):
    url = f"{agent_url}/get_rates"
    payload = {"symbol": symbol, "timeframe": timeframe, "n_candles": n_candles}
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        rates = data.get("rates", [])
        if not rates:
            return None
        df = pd.DataFrame(rates)
        if "time" not in df.columns:
            return None
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df
    except Exception:
        return None


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ─────────────────────── SEND ORDER (unified) ───────────────────────

def send_order(trader_id: int, direction: str):
    with sessions_lock:
        if trader_id not in sessions:
            return
        session = sessions[trader_id]
        trader = session["trader"]
        trader_data = session["trader_data"]
        effective_sl = session.get("effective_sl")
        effective_tp = session.get("effective_tp")

    symbol = trader.selected_symbol
    slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"

    try:
        info_resp = requests.get(f"{slave_url}/symbol_info/{symbol}", timeout=10)
        tick_resp = requests.get(f"{slave_url}/symbol_tick/{symbol}", timeout=10)
        if info_resp.status_code != 200 or tick_resp.status_code != 200:
            log(trader_id, f"Impossibile recuperare dati dallo slave")
            return
        sym_info = info_resp.json()
        tick = tick_resp.json()
    except Exception as e:
        log(trader_id, f"Errore connessione slave: {e}")
        return

    pip_value = float(sym_info.get("point", 0.00001))
    sl_points = effective_sl if effective_sl is not None else trader.sl
    tp_points = effective_tp if effective_tp is not None else trader.tp

    if direction == "buy":
        price = tick["ask"]
        sl_value = price - (float(sl_points) * pip_value) if sl_points and sl_points > 0 else None
        tp_value = price + (float(tp_points) * pip_value) if tp_points and tp_points > 0 else None
    else:
        price = tick["bid"]
        sl_value = price + (float(sl_points) * pip_value) if sl_points and sl_points > 0 else None
        tp_value = price - (float(tp_points) * pip_value) if tp_points and tp_points > 0 else None

    log(trader_id, f"{direction.upper()}: SL={sl_points}pts ({sl_value}), TP={tp_points}pts ({tp_value})")

    payload = {
        "trader_id": trader_id,
        "order_type": direction,
        "volume": trader.fix_lot,
        "symbol": symbol,
        "sl": sl_value,
        "tp": tp_value,
        "broker": trader.broker,
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/db/traders/{trader_id}/open_order_on_slave",
            json=payload, timeout=10
        )
        log(trader_id, f"{direction.upper()} Response: {resp.text}")
    except Exception as e:
        log(trader_id, f"Errore invio ordine: {e}")


def close_slave_position(trader_id: int):
    with sessions_lock:
        if trader_id not in sessions:
            return
        trader = sessions[trader_id]["trader"]

    payload = {"symbol": trader.selected_symbol, "trader_id": trader_id}
    try:
        resp = requests.post(
            f"{BASE_URL}/db/traders/{trader_id}/close_order_on_slave",
            json=payload, timeout=10
        )
        log(trader_id, f"CLOSE Response: {resp.text}")
    except Exception as e:
        log(trader_id, f"Errore chiusura: {e}")


# ─────────────────────── PER-TRADER LOG ───────────────────────

def log(trader_id: int, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    with sessions_lock:
        if trader_id in sessions:
            sessions[trader_id].setdefault("logs", []).append(f"[{ts}] {msg}")


# ─────────────────────── STRATEGY BASE CLASS ───────────────────────

class Indicators:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class SignalStrategy:
    name = "BASE"
    requires_m1 = False
    requires_m5 = True
    requires_m15 = False

    def compute_indicators(self, df_m1, df_m5, df_m15) -> Indicators:
        raise NotImplementedError

    def buy_condition(self, ind: Indicators) -> bool:
        raise NotImplementedError

    def sell_condition(self, ind: Indicators) -> bool:
        raise NotImplementedError

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return has_sell

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return has_buy

    def on_hold_action(self, ind: Indicators, has_buy: bool, has_sell: bool, prev_signal: str):
        return None

    def get_dynamic_sl_tp(self, ind: Indicators):
        return None, None

    def run(self, trader_id: int):
        with sessions_lock:
            if trader_id not in sessions:
                return
            session = sessions[trader_id]
            trader = session["trader"]
            trader_data = session["trader_data"]
            prev_signal = session.get("prev_signal", "HOLD")
            session["logs"] = []

        slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"
        symbol = trader.selected_symbol
        now = now_str()

        # ── skip duplicato M1 ──
        if self.requires_m1:
            current_m1_ts = datetime.now().replace(second=0, microsecond=0)
            if session.get("last_processed_m1") == current_m1_ts:
                return
            session["last_processed_m1"] = current_m1_ts

        # ── fetch dati ──
        df_m1 = get_data(symbol, 1, 100, slave_url) if self.requires_m1 else None
        df_m5 = get_data(symbol, 5, 100, slave_url) if self.requires_m5 else None
        df_m15 = get_data(symbol, 15, 50, slave_url) if self.requires_m15 else None

        if self.requires_m1 and (df_m1 is None or df_m1.empty):
            return
        if self.requires_m5 and (df_m5 is None or df_m5.empty):
            return
        if self.requires_m15 and (df_m15 is None or df_m15.empty):
            return

        # ── indicatori ──
        ind = self.compute_indicators(df_m1, df_m5, df_m15)

        # ── SL/TP dinamico ──
        effective_sl, effective_tp = self.get_dynamic_sl_tp(ind)
        with sessions_lock:
            if trader_id in sessions:
                sessions[trader_id]["effective_sl"] = effective_sl
                sessions[trader_id]["effective_tp"] = effective_tp

        # ── posizioni slave ──
        try:
            resp = requests.get(f"{slave_url}/positions", timeout=5)
            if resp.status_code != 200:
                log(trader_id, f"Slave non risponde")
                return
            positions = resp.json()
        except:
            log(trader_id, f"Slave non raggiungibile")
            return

        has_buy = any(p["symbol"] == symbol and p["type"] == 0 for p in positions)
        has_sell = any(p["symbol"] == symbol and p["type"] == 1 for p in positions)

        # ── decisione ──
        new_signal = "HOLD"
        log(trader_id, f"S-I-G-N-A-L [{self.name}] | Trader {trader_id} | {symbol}")

        if self.buy_condition(ind):
            new_signal = "BUY"
            log(trader_id, f"BUY signal per {symbol}")

            if not has_buy:
                if self.reverse_on_buy(has_sell):
                    close_slave_position(trader_id)
                send_order(trader_id, "buy")

        elif self.sell_condition(ind):
            new_signal = "SELL"
            log(trader_id, f"SELL signal per {symbol}")

            if not has_sell:
                if self.reverse_on_sell(has_buy):
                    close_slave_position(trader_id)
                send_order(trader_id, "sell")

        else:
            log(trader_id, f"HOLD signal per {symbol}")
            action = self.on_hold_action(ind, has_buy, has_sell, prev_signal)
            if action == "close_buy":
                close_slave_position(trader_id)
            elif action == "close_sell":
                close_slave_position(trader_id)

        with sessions_lock:
            if trader_id in sessions:
                sessions[trader_id]["prev_signal"] = new_signal


# ─────────────────────── CONCRETE STRATEGIES ───────────────────────

class BaseStrategy(SignalStrategy):
    name = "BASE"

    def compute_indicators(self, df_m1, df_m5, df_m15):
        return Indicators(
            ema_short=compute_ema(df_m5, 5).iloc[-1],
            ema_long=compute_ema(df_m5, 15).iloc[-1],
            rsi=compute_rsi(df_m5, 14).iloc[-1],
        )

    def buy_condition(self, ind: Indicators) -> bool:
        return ind.ema_short > ind.ema_long and ind.rsi < 68

    def sell_condition(self, ind: Indicators) -> bool:
        return ind.ema_short < ind.ema_long and ind.rsi > 32

    def on_hold_action(self, ind, has_buy, has_sell, prev_signal):
        if prev_signal == "BUY" and has_buy:
            return "close_buy"
        if prev_signal == "SELL" and has_sell:
            return "close_sell"
        return None


class NoReverseStrategy(SignalStrategy):
    name = "BASE_NOHOLD"

    def compute_indicators(self, df_m1, df_m5, df_m15):
        return Indicators(
            ema_short=compute_ema(df_m5, 5).iloc[-1],
            ema_long=compute_ema(df_m5, 15).iloc[-1],
            rsi=compute_rsi(df_m5, 14).iloc[-1],
        )

    def buy_condition(self, ind: Indicators) -> bool:
        return ind.ema_short > ind.ema_long and ind.rsi < 68

    def sell_condition(self, ind: Indicators) -> bool:
        return ind.ema_short < ind.ema_long and ind.rsi > 32

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False


class EurUsdStrategy(SignalStrategy):
    name = "EURUSD_NOHOLD"

    def compute_indicators(self, df_m1, df_m5, df_m15):
        return Indicators(
            ema_short=compute_ema(df_m5, 5).iloc[-1],
            ema_long=compute_ema(df_m5, 20).iloc[-1],
            rsi=compute_rsi(df_m5, 14).iloc[-1],
        )

    def buy_condition(self, ind: Indicators) -> bool:
        return ind.ema_short > ind.ema_long and ind.rsi < 65

    def sell_condition(self, ind: Indicators) -> bool:
        return ind.ema_short < ind.ema_long and ind.rsi > 35


class SuperXauNoCloseStrategy(SignalStrategy):
    name = "SUPER"
    requires_m1 = True
    requires_m5 = True
    requires_m15 = True

    def compute_indicators(self, df_m1, df_m5, df_m15):
        ema_fast = compute_ema(df_m1, 9).iloc[-2]
        ema_slow = compute_ema(df_m1, 21).iloc[-2]
        rsi_m1 = compute_rsi(df_m1, 14).iloc[-2]
        macd, macd_sig = compute_macd(df_m1)

        hma_m5 = compute_hma(df_m5).iloc[-2]
        hma_m5_prev = compute_hma(df_m5).iloc[-3]

        ema_m15 = compute_ema(df_m15, 50).iloc[-2]
        price_m15 = df_m15["close"].iloc[-2]

        atr_series = compute_atr(df_m1)
        atr = atr_series.iloc[-2]
        volatilty_expansion = atr > atr_series.rolling(10).mean().iloc[-2]

        candle_body = abs(df_m1["close"].iloc[-2] - df_m1["open"].iloc[-2])
        is_spike = candle_body > (atr * 3)

        # ATR M5
        df_m5_tmp = df_m5.copy()
        df_m5_tmp["prev_close"] = df_m5_tmp["close"].shift(1)
        df_m5_tmp["tr"] = df_m5_tmp.apply(lambda r: max(
            r["high"] - r["low"],
            abs(r["high"] - r["prev_close"]),
            abs(r["low"] - r["prev_close"])
        ), axis=1)
        atr_m5_val = df_m5_tmp["tr"].rolling(14).mean().iloc[-2]

        return Indicators(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            rsi_m1=rsi_m1,
            macd=macd.iloc[-2],
            macd_sig=macd_sig.iloc[-2],
            hma_m5=hma_m5,
            hma_m5_prev=hma_m5_prev,
            trend_macro_up=price_m15 > ema_m15,
            volatilty_expansion=volatilty_expansion,
            is_spike=is_spike,
            atr_m5_val=atr_m5_val,
        )

    def buy_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_fast > ind.ema_slow
            and ind.macd > ind.macd_sig
            and ind.hma_m5 > ind.hma_m5_prev
            and ind.trend_macro_up
            and 45 < ind.rsi_m1 < 70
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def sell_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_fast < ind.ema_slow
            and ind.macd < ind.macd_sig
            and ind.hma_m5 < ind.hma_m5_prev
            and not ind.trend_macro_up
            and 30 < ind.rsi_m1 < 55
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False

    def get_dynamic_sl_tp(self, ind: Indicators):
        if ind.atr_m5_val <= 6:
            return 300, 400
        return None, None


# ─────────────────────── STRATEGY MAP ───────────────────────

STRATEGIES = {
    "BASE": BaseStrategy(),
    "BASE_NOHOLD": NoReverseStrategy(),
    "TRENDGUARD": BaseStrategy(),
    "EURUSD_NOHOLD": EurUsdStrategy(),
    "SUPER": SuperXauNoCloseStrategy(),
}

DEFAULT_STRATEGY = BaseStrategy()


# ─────────────────────── POLLING LOOP ───────────────────────

def run_signal_logic(trader_id: int):
    with sessions_lock:
        if trader_id not in sessions:
            return
        trader = sessions[trader_id]["trader"]

    chosen = trader.selected_signal or "BASE"
    strategy = STRATEGIES.get(chosen, DEFAULT_STRATEGY)
    strategy.run(trader_id)


def polling_loop_timer(trader_id: int):
    with sessions_lock:
        if trader_id not in sessions:
            return
        session = sessions[trader_id]
        trader = session["trader"]

    run_signal_logic(trader_id)

    interval = int(trader.custom_signal_interval or 5)
    with sessions_lock:
        if trader_id in sessions:
            t = threading.Timer(interval, polling_loop_timer, args=[trader_id])
            sessions[trader_id]["timer"] = t
            t.start()


# ─────────────────────── API ENDPOINTS ───────────────────────

class StopPollingRequest(BaseModel):
    trader_id: int


@router.post("/start_polling")
def start_polling(trader: Trader):
    tid = trader.id
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    trader_data = get_trader(cursor, tid)
    if not trader_data:
        return {"status": "error", "message": "Trader non trovato"}

    with sessions_lock:
        if tid in sessions and sessions[tid].get("timer"):
            sessions[tid]["timer"].cancel()

        sessions[tid] = {
            "trader": trader,
            "trader_data": trader_data,
            "prev_signal": "HOLD",
            "timer": None,
            "logs": [],
        }

    polling_loop_timer(tid)
    return {"status": "started", "trader_id": tid}


@router.post("/stop_polling")
def stop_polling(req: StopPollingRequest):
    trader_id = req.trader_id
    with sessions_lock:
        if trader_id in sessions:
            timer = sessions[trader_id].get("timer")
            if timer:
                timer.cancel()
            del sessions[trader_id]
            return {"status": "stopped", "trader_id": trader_id}
    return {"status": "not_running"}
