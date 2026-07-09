# --- STATO MULTI-SESSIONE (REFACTORED: STRATEGY PATTERN) ---
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import threading
import time

from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi import APIRouter
from logger import log as global_log, logs
from db import get_trader, get_connection
from models import Trader
from indicators.ta import (
    compute_ema, compute_rsi, compute_macd, compute_atr,
    compute_bollinger, compute_hma, compute_adx,
    compute_ichimoku
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
    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M:%S")


def is_market_open(symbol: str) -> bool:
    now = datetime.now(ZoneInfo("Europe/Rome"))
    weekday = now.isoweekday()
    hour = now.hour

    # Saturday (6) sempre chiuso
    if weekday == 6:
        return False

    # Sunday (7) chiuso fino alle 23:00 Rome time (forex open)
    if weekday == 7 and hour < 23:
        return False

    return True


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
    use_profit_tp = getattr(trader, 'use_profit_tp', False)
    profit_tp_value = getattr(trader, 'profit_tp_value', None)
    if use_profit_tp and profit_tp_value and profit_tp_value > 0:
        tp_points = None
    else:
        tp_points = effective_tp if effective_tp is not None else trader.tp

    if direction == "buy":
        price = tick["ask"]
        sl_value = price - (float(sl_points) * pip_value) if sl_points and sl_points > 0 else None
        tp_value = price + (float(tp_points) * pip_value) if tp_points and tp_points > 0 else None
    else:
        price = tick["bid"]
        sl_value = price + (float(sl_points) * pip_value) if sl_points and sl_points > 0 else None
        tp_value = price - (float(tp_points) * pip_value) if tp_points and tp_points > 0 else None

    log(trader_id, f"📐 Trader {trader_id} {direction.upper()}: SL={sl_points}pts ({sl_value}), TP={tp_points}pts ({tp_value})")

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
        log(trader_id, f"📥 Trader {trader_id} {direction.upper()} Response: {resp.text}")
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
        log(trader_id, f"📥 Trader {trader_id} CLOSE Response: {resp.text}")
    except Exception as e:
        log(trader_id, f"Errore chiusura: {e}")


# ─────────────────────── PER-TRADER LOG ───────────────────────

def log(trader_id: int, msg: str):
    ts = datetime.now(ZoneInfo("Europe/Rome")).strftime("%H:%M:%S")
    global_log(msg)
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
    requires_h1 = False

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None) -> Indicators:
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

    def get_log_details(self, ind: Indicators) -> str:
        return ""

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        d = f" {details}" if details else ""
        return f"S-I-G-N-A-L [{self.name}] | {d}".strip()

    def run(self, trader_id: int):
        with sessions_lock:
            if trader_id not in sessions:
                return
            session = sessions[trader_id]
            trader = session["trader"]
            trader_data = session["trader_data"]
            prev_signal = session.get("prev_signal", "HOLD")
            logs.clear()

        slave_url = f"http://{trader_data['slave_ip']}:{trader_data['slave_port']}"
        symbol = trader.selected_symbol
        now = now_str()

        # ── skip duplicato M1 ──
        if self.requires_m1:
            current_ts = datetime.now().replace(second=0, microsecond=0)
            if session.get("last_processed_m1") == current_ts:
                return
            session["last_processed_m1"] = current_ts

        # ── mercato aperto? ──
        if not is_market_open(symbol):
            log(trader_id, f"⏸ Mercato chiuso (weekend) per {symbol}")
            return

        # ── fetch dati ──
        df_m1 = get_data(symbol, 1, 100, slave_url) if self.requires_m1 else None
        df_m5 = get_data(symbol, 5, 100, slave_url) if self.requires_m5 else None
        df_m15 = get_data(symbol, 15, 50, slave_url) if self.requires_m15 else None
        df_h1 = get_data(symbol, 16385, 120, slave_url) if self.requires_h1 else None

        if self.requires_m1 and (df_m1 is None or df_m1.empty):
            return
        if self.requires_m5 and (df_m5 is None or df_m5.empty):
            return
        if self.requires_m15 and (df_m15 is None or df_m15.empty):
            return
        if self.requires_h1 and (df_h1 is None or df_h1.empty):
            return

        # ── skip H1 se candela non ancora chiusa ──
        if self.requires_h1:
            latest_h1_ts = df_h1["time"].iloc[-1]
            if session.get("last_processed_h1") == latest_h1_ts:
                skip_count = session.get("h1_skip_count", 0) + 1
                session["h1_skip_count"] = skip_count
                if skip_count % 5 == 0:
                    log(trader_id, f"⏳ In attesa della prossima candela H1...")
                return
            session["last_processed_h1"] = latest_h1_ts
            session["h1_skip_count"] = 0

        # ── indicatori ──
        ind = self.compute_indicators(df_m1, df_m5, df_m15, df_h1=df_h1)

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

        # ── Profit TP check (sovrascrive qualsiasi TP) ──
        use_profit_tp = getattr(trader, 'use_profit_tp', False)
        profit_tp_value = getattr(trader, 'profit_tp_value', None)
        if use_profit_tp and profit_tp_value and profit_tp_value > 0:
            for p in positions:
                if p["symbol"] == symbol and p.get("profit", 0) >= profit_tp_value:
                    log(trader_id, f"💰 Profit TP {profit_tp_value}$ raggiunto per {symbol} (profit: {p['profit']:.2f})")
                    close_slave_position(trader_id)
                    has_buy = False
                    has_sell = False
                    break

        # ── decisione ──
        new_signal = "HOLD"
        log_details = self.get_log_details(ind)
        header = self.get_log_header(ind)
        log(trader_id, f"{header} | {trader.name} | {symbol}")

        if self.buy_condition(ind):
            new_signal = "BUY"
            log(trader_id, f"🔥 BUY signal per {symbol} {log_details}")

            if not has_buy:
                if self.reverse_on_buy(has_sell):
                    close_slave_position(trader_id)
                send_order(trader_id, "buy")

        elif self.sell_condition(ind):
            new_signal = "SELL"
            log(trader_id, f"🔻 SELL signal per {symbol} {log_details}")

            if not has_sell:
                if self.reverse_on_sell(has_buy):
                    close_slave_position(trader_id)
                send_order(trader_id, "sell")

        else:
            log(trader_id, f"🔥 HOLD signal per {symbol} {log_details}")
            action = self.on_hold_action(ind, has_buy, has_sell, prev_signal)
            if action == "close_buy":
                close_slave_position(trader_id)
            elif action == "close_sell":
                close_slave_position(trader_id)

        with sessions_lock:
            if trader_id in sessions:
                sessions[trader_id]["prev_signal"] = new_signal


# ─────────────────────── CONCRETE STRATEGIES ───────────────────────

class NoReverseStrategy(SignalStrategy):
    name = "BASE_NOHOLD"

    def __init__(self, close_on_hold=False):
        self.close_on_hold = close_on_hold

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
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

    def on_hold_action(self, ind, has_buy, has_sell, prev_signal):
        if not self.close_on_hold:
            return None
        if prev_signal == "BUY" and has_buy:
            return "close_buy"
        if prev_signal == "SELL" and has_sell:
            return "close_sell"
        return None


class EurUsdStrategy(SignalStrategy):
    name = "EURUSD_NOHOLD"

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
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

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
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
        if ind.atr_m5_val <= 12:
            return 1000, 1500
        return None, None

    def get_log_details(self, ind: Indicators) -> str:
        return f"(ATR M5: {ind.atr_m5_val:.1f})"

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        return f"S-I-G-N-A-L [{self.name}] | {details}"


class SuperXauProStrategy(SignalStrategy):
    name = "SUPER_PRO"
    requires_m1 = True
    requires_m5 = True
    requires_m15 = True

    def _get_session_params(self):
        now = datetime.now(ZoneInfo("Europe/Rome"))
        hour = now.hour
        minute = now.minute

        # Asia: 01:00-09:00 — bassa volatilità
        if 1 <= hour < 9:
            return {
                "label": "ASIA",
                "rsi_period": 21,
                "rsi_buy_min": 50, "rsi_buy_max": 78,
                "rsi_sell_min": 22, "rsi_sell_max": 50,
                "vol_expansion_mult": 1.3,
                "sl_atr_factor": 4.0,
                "tp_atr_factor": 2.0,
            }
        # London: 09:00-14:00 — media volatilità
        elif 9 <= hour < 14:
            return {
                "label": "LONDON",
                "rsi_period": 14,
                "rsi_buy_min": 45, "rsi_buy_max": 72,
                "rsi_sell_min": 28, "rsi_sell_max": 55,
                "vol_expansion_mult": 1.0,
                "sl_atr_factor": 3.0,
                "tp_atr_factor": 1.8,
            }
        # NY-London overlap: 14:00-17:30 — massima volatilità
        elif 14 <= hour < 17 or (hour == 17 and minute < 30):
            return {
                "label": "NY-LON",
                "rsi_period": 9,
                "rsi_buy_min": 42, "rsi_buy_max": 68,
                "rsi_sell_min": 32, "rsi_sell_max": 58,
                "vol_expansion_mult": 0.8,
                "sl_atr_factor": 2.5,
                "tp_atr_factor": 2.2,
            }
        # NY late: 17:30-22:00
        elif (hour == 17 and minute >= 30) or 18 <= hour < 22:
            return {
                "label": "NY",
                "rsi_period": 14,
                "rsi_buy_min": 45, "rsi_buy_max": 70,
                "rsi_sell_min": 30, "rsi_sell_max": 55,
                "vol_expansion_mult": 1.0,
                "sl_atr_factor": 3.0,
                "tp_atr_factor": 1.5,
            }
        # Chiuso / bassa liquidità: 22:00-01:00
        else:
            return {
                "label": "OFF",
                "rsi_period": 21,
                "rsi_buy_min": 55, "rsi_buy_max": 85,
                "rsi_sell_min": 15, "rsi_sell_max": 45,
                "vol_expansion_mult": 1.5,
                "sl_atr_factor": 5.0,
                "tp_atr_factor": 1.2,
            }

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
        params = self._get_session_params()

        # ── M1 ──
        ema_fast = compute_ema(df_m1, 9).iloc[-2]
        ema_slow = compute_ema(df_m1, 21).iloc[-2]
        rsi_m1 = compute_rsi(df_m1, params["rsi_period"]).iloc[-2]
        macd, macd_sig = compute_macd(df_m1)

        # ── M5 ──
        hma_m5 = compute_hma(df_m5).iloc[-2]
        hma_m5_prev = compute_hma(df_m5).iloc[-3]

        # ── M15: EMA200 macro trend filter (vs SUPER che usa EMA50) ──
        ema_m15_200 = compute_ema(df_m15, 200).iloc[-2]
        price_m15 = df_m15["close"].iloc[-2]
        trend_macro_up = price_m15 > ema_m15_200

        # M15 EMA50 per conferma aggiuntiva
        ema_m15_50 = compute_ema(df_m15, 50).iloc[-2]
        trend_macro_50_up = price_m15 > ema_m15_50

        # ── Volatilità M1 ──
        atr_series = compute_atr(df_m1)
        atr = atr_series.iloc[-2]
        atr_mean = atr_series.rolling(10).mean().iloc[-2]
        volatility_expansion = atr > atr_mean * params["vol_expansion_mult"]

        candle_body = abs(df_m1["close"].iloc[-2] - df_m1["open"].iloc[-2])
        is_spike = candle_body > (atr * 3)

        # ── ATR M5 per SL/TP ──
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
            trend_macro_up=trend_macro_up,
            trend_macro_50_up=trend_macro_50_up,
            volatility_expansion=volatility_expansion,
            is_spike=is_spike,
            atr_m5_val=atr_m5_val,
            atr_m1=atr,
            session_label=params["label"],
        )

    def buy_condition(self, ind: Indicators) -> bool:
        params = self._get_session_params()
        # 1) Trend filter: solo long se sopra EMA200
        if not ind.trend_macro_up:
            return False
        # 2) Multi-timeframe: EMA50 M15 deve confermare
        if not ind.trend_macro_50_up:
            return False
        return (
            ind.ema_fast > ind.ema_slow
            and ind.macd > ind.macd_sig
            and ind.hma_m5 > ind.hma_m5_prev
            and params["rsi_buy_min"] < ind.rsi_m1 < params["rsi_buy_max"]
            and ind.volatility_expansion
            and not ind.is_spike
        )

    def sell_condition(self, ind: Indicators) -> bool:
        params = self._get_session_params()
        # 1) Trend filter: solo short se sotto EMA200
        if ind.trend_macro_up:
            return False
        # 2) Multi-timeframe: EMA50 M15 deve confermare
        if ind.trend_macro_50_up:
            return False
        return (
            ind.ema_fast < ind.ema_slow
            and ind.macd < ind.macd_sig
            and ind.hma_m5 < ind.hma_m5_prev
            and params["rsi_sell_min"] < ind.rsi_m1 < params["rsi_sell_max"]
            and ind.volatility_expansion
            and not ind.is_spike
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False

    def get_dynamic_sl_tp(self, ind: Indicators):
        params = self._get_session_params()
        atr = ind.atr_m5_val
        if atr <= 0:
            return None, None
        sl = int(atr * params["sl_atr_factor"] * 10)
        tp = int(atr * params["tp_atr_factor"] * 10)
        sl = max(300, min(sl, 2000))
        tp = max(sl + 100, min(tp, 3000))
        return sl, tp

    def on_hold_action(self, ind, has_buy, has_sell, prev_signal):
        # 4) Exit rule: chiudi se trend macro inverte
        if has_buy and not ind.trend_macro_up and ind.hma_m5 < ind.hma_m5_prev:
            return "close_buy"
        if has_sell and ind.trend_macro_up and ind.hma_m5 > ind.hma_m5_prev:
            return "close_sell"
        return None

    def get_log_details(self, ind: Indicators) -> str:
        return f"(ATR:{ind.atr_m5_val:.1f} S:{ind.session_label} RSI:{ind.rsi_m1:.0f} Trend:{'UP' if ind.trend_macro_up else 'DOWN'})"

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        return f"S-I-G-N-A-L [{self.name}] | {details}"


class MsftStrategy(SignalStrategy):
    name = "MSFT"
    requires_m15 = True

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
        df = df_m15
        volume_avg = df["tick_volume"].rolling(20).mean().iloc[-1]
        volume_now = df["tick_volume"].iloc[-1]

        return Indicators(
            ema_short=compute_ema(df, 5).iloc[-1],
            ema_long=compute_ema(df, 20).iloc[-1],
            rsi=compute_rsi(df, 14).iloc[-1],
            hma=compute_hma(df).iloc[-1],
            hma_prev=compute_hma(df).iloc[-2],
            volume_ok=volume_now > volume_avg * 1.2 if volume_avg > 0 else True,
        )

    def buy_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_short > ind.ema_long
            and ind.hma > ind.hma_prev
            and 40 < ind.rsi < 75
            and ind.volume_ok
        )

    def sell_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_short < ind.ema_long
            and ind.hma < ind.hma_prev
            and 25 < ind.rsi < 60
            and ind.volume_ok
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False


class NvdaStrategy(SignalStrategy):
    name = "NVDA"
    requires_m15 = True

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
        df = df_m15
        volume_avg = df["tick_volume"].rolling(20).mean().iloc[-1]
        volume_now = df["tick_volume"].iloc[-1]

        return Indicators(
            ema_short=compute_ema(df, 5).iloc[-1],
            ema_long=compute_ema(df, 20).iloc[-1],
            rsi=compute_rsi(df, 14).iloc[-1],
            hma=compute_hma(df).iloc[-1],
            hma_prev=compute_hma(df).iloc[-2],
            volume_ok=volume_now > volume_avg * 1.3 if volume_avg > 0 else True,
        )

    def buy_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_short > ind.ema_long
            and ind.hma > ind.hma_prev
            and 35 < ind.rsi < 78
            and ind.volume_ok
        )

    def sell_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_short < ind.ema_long
            and ind.hma < ind.hma_prev
            and 22 < ind.rsi < 62
            and ind.volume_ok
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False


class SuperUsdJpyStrategy(SignalStrategy):
    name = "SUPER_USDJPY"
    requires_m1 = True
    requires_m5 = True
    requires_m15 = True

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
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
            and 40 < ind.rsi_m1 < 68
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def sell_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_fast < ind.ema_slow
            and ind.macd < ind.macd_sig
            and ind.hma_m5 < ind.hma_m5_prev
            and not ind.trend_macro_up
            and 32 < ind.rsi_m1 < 60
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False

    def get_dynamic_sl_tp(self, ind: Indicators):
        if ind.atr_m5_val <= 0.050:
            return 500, 600
        return None, None

    def get_log_details(self, ind: Indicators) -> str:
        return f"(ATR M5: {ind.atr_m5_val:.4f})"

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        return f"S-I-G-N-A-L [{self.name}] | {details}"


class GbpUsdStrategy(SignalStrategy):
    name = "GBPUSD"
    requires_m1 = True
    requires_m5 = True
    requires_m15 = True

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
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
            and 40 < ind.rsi_m1 < 70
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def sell_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_fast < ind.ema_slow
            and ind.macd < ind.macd_sig
            and ind.hma_m5 < ind.hma_m5_prev
            and not ind.trend_macro_up
            and 30 < ind.rsi_m1 < 60
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False

    def get_dynamic_sl_tp(self, ind: Indicators):
        if ind.atr_m5_val <= 0.0012:
            return 500, 600
        return None, None

    def get_log_details(self, ind: Indicators) -> str:
        return f"(ATR M5: {ind.atr_m5_val:.5f})"

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        return f"S-I-G-N-A-L [{self.name}] | {details}"


class GbpJpyStrategy(SignalStrategy):
    name = "GBPJPY"
    requires_m1 = True
    requires_m5 = True
    requires_m15 = True

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
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
            and 35 < ind.rsi_m1 < 75
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def sell_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_fast < ind.ema_slow
            and ind.macd < ind.macd_sig
            and ind.hma_m5 < ind.hma_m5_prev
            and not ind.trend_macro_up
            and 25 < ind.rsi_m1 < 65
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False

    def get_dynamic_sl_tp(self, ind: Indicators):
        if ind.atr_m5_val <= 0.080:
            return 800, 1000
        return None, None

    def get_log_details(self, ind: Indicators) -> str:
        return f"(ATR M5: {ind.atr_m5_val:.4f})"

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        return f"S-I-G-N-A-L [{self.name}] | {details}"


class AudJpyStrategy(SignalStrategy):
    name = "AUDJPY"
    requires_m1 = True
    requires_m5 = True
    requires_m15 = True

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
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
            and 38 < ind.rsi_m1 < 72
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def sell_condition(self, ind: Indicators) -> bool:
        return (
            ind.ema_fast < ind.ema_slow
            and ind.macd < ind.macd_sig
            and ind.hma_m5 < ind.hma_m5_prev
            and not ind.trend_macro_up
            and 28 < ind.rsi_m1 < 62
            and ind.volatilty_expansion
            and not ind.is_spike
        )

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return False

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return False

    def get_dynamic_sl_tp(self, ind: Indicators):
        if ind.atr_m5_val <= 0.060:
            return 600, 800
        return None, None

    def get_log_details(self, ind: Indicators) -> str:
        return f"(ATR M5: {ind.atr_m5_val:.4f})"

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        return f"S-I-G-N-A-L [{self.name}] | {details}"


class IchimokuXauStrategy(SignalStrategy):
    name = "ICHIMOKU"
    requires_m1 = False
    requires_m5 = False
    requires_m15 = False
    requires_h1 = True

    def compute_indicators(self, df_m1, df_m5, df_m15, df_h1=None):
        tenkan, kijun, senkou_a, senkou_b, chikou = compute_ichimoku(df_h1)
        return Indicators(
            tenkan=tenkan.iloc[-2],
            kijun=kijun.iloc[-2],
            senkou_a=senkou_a.iloc[-2],
            senkou_b=senkou_b.iloc[-2],
            chikou=chikou.iloc[-2],
            chikou_prev=chikou.iloc[-3],
            price=df_h1["close"].iloc[-2],
        )

    def buy_condition(self, ind: Indicators) -> bool:
        above_cloud = ind.price > ind.senkou_a and ind.price > ind.senkou_b
        tk_bull = ind.tenkan > ind.kijun
        return above_cloud and tk_bull

    def sell_condition(self, ind: Indicators) -> bool:
        below_cloud = ind.price < ind.senkou_a and ind.price < ind.senkou_b
        tk_bear = ind.tenkan < ind.kijun
        return below_cloud and tk_bear

    def reverse_on_buy(self, has_sell: bool) -> bool:
        return True

    def reverse_on_sell(self, has_buy: bool) -> bool:
        return True

    def get_log_details(self, ind: Indicators) -> str:
        cloud_pos = "ABOVE" if ind.price > max(ind.senkou_a, ind.senkou_b) else "BELOW" if ind.price < min(ind.senkou_a, ind.senkou_b) else "INSIDE"
        return f"(T:{ind.tenkan:.1f} K:{ind.kijun:.1f} Cloud:{cloud_pos})"

    def get_log_header(self, ind: Indicators) -> str:
        details = self.get_log_details(ind)
        return f"S-I-G-N-A-L [{self.name}] | {details}"


# ─────────────────────── STRATEGY MAP ───────────────────────

STRATEGIES = {
    "BASE": NoReverseStrategy(close_on_hold=True),
    "BASE_NOHOLD": NoReverseStrategy(),
    "TRENDGUARD": NoReverseStrategy(close_on_hold=True),
    "TRENDGUARD_XAU": NoReverseStrategy(close_on_hold=True),
    "EURUSD_NOHOLD": EurUsdStrategy(),
    "SUPER": SuperXauNoCloseStrategy(),
    "SUPER_PRO": SuperXauProStrategy(),
    "ICHIMOKU": IchimokuXauStrategy(),
    "MSFT": MsftStrategy(),
    "NVDA": NvdaStrategy(),
    "SUPER_USDJPY": SuperUsdJpyStrategy(),
    "GBPUSD": GbpUsdStrategy(),
    "GBPJPY": GbpJpyStrategy(),
    "AUDJPY": AudJpyStrategy(),
}

DEFAULT_STRATEGY = NoReverseStrategy(close_on_hold=True)


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

    global_log(f"▶ START {trader.name} | {trader.selected_symbol}")
    polling_loop_timer(tid)
    return {"status": "started", "trader_id": tid}


@router.post("/stop_polling")
def stop_polling(req: StopPollingRequest):
    trader_id = req.trader_id
    with sessions_lock:
        if trader_id in sessions:
            trader = sessions[trader_id].get("trader")
            timer = sessions[trader_id].get("timer")
            if timer:
                timer.cancel()
            trader_name = trader.name if trader else str(trader_id)
            trader_symbol = trader.selected_symbol if trader else "?"
            del sessions[trader_id]
            global_log(f"⏹ STOP {trader_name} | {trader_symbol}")
            return {"status": "stopped", "trader_id": trader_id}
    return {"status": "not_running"}
