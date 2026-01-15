# # backend/app.py
# from datetime import datetime
# import json
# import os
# from dotenv import load_dotenv
# from fastapi import APIRouter, FastAPI, HTTPException
# from fastapi.middleware.cors import CORSMiddleware
# # import MetaTrader5 as mt5
# from logger import log, logs
# from logger import safe_get
# from db import get_trader, get_connection
# from models import (
#     Trader
# )
# import pandas as pd
# import threading
# import time
# import requests

# # =========================
# # Multi-instance polling
# # =========================
# active_pollings: dict[int, threading.Thread] = {}
# polling_flags: dict[int, bool] = {}  # True = running, False = stopped

# def polling_worker(trader: Trader):
#     trader_id = trader.id
#     log(f"▶️ Polling worker avviato per trader {trader_id}")

#     # Thread-local variables
#     symbol = trader.selectedSymbol
#     interval = int(trader.customSignalInterval or 2)
#     base_url_slave = f"http://{trader.slave_ip}:{trader.slave_port}"
#     previous_signal = "HOLD"
#     current_signal = "HOLD"

#     while polling_flags.get(trader_id, False):
#         # Qui usiamo check_signal_super ma isolato per questo trader
#         try:
#             check_signal_super_multi(trader, symbol, base_url_slave, previous_signal, current_signal)
#         except Exception as e:
#             log(f"❌ Errore polling trader {trader_id}: {e}")
#         time.sleep(interval)

#     log(f"⏹️ Polling worker fermato per trader {trader_id}")

# def start_polling_multi(trader: Trader):
#     trader_id = trader.id

#     if polling_flags.get(trader_id):
#         return {"status": "already_running", "message": f"Polling già attivo per trader {trader_id}"}

#     polling_flags[trader_id] = True
#     thread = threading.Thread(target=polling_worker, args=(trader,), daemon=True)
#     thread.start()
#     active_pollings[trader_id] = thread

#     log(f"▶️ Polling multi avviato per trader {trader_id}")
#     return {"status": "started", "trader_id": trader_id}

# def stop_polling_multi(trader_id: int):
#     if not polling_flags.get(trader_id):
#         return {"status": "not_running", "message": f"Nessun polling attivo per trader {trader_id}"}

#     polling_flags[trader_id] = False
#     log(f"⏹️ Richiesta stop polling per trader {trader_id}")
#     return {"status": "stopped", "trader_id": trader_id}


# # =========================
# # check_signal_super adattato al multi
# # =========================
# def check_signal_super_multi(trader: Trader, symbol: str, base_url_slave: str, previous_signal: str, current_signal: str):
#     """
#     Versione isolata di check_signal_super per singolo trader.
#     Tutte le variabili globali sono passate come parametro.
#     """
#     logs.clear()
#     now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

#     df = get_data(symbol, TIMEFRAME, N_CANDLES, base_url_slave)
#     if df is None:
#         return

#     ema_short = compute_ema(df, PARAMETERS["EMA_short"])
#     ema_long  = compute_ema(df, PARAMETERS["EMA_long"])
#     rsi       = compute_rsi(df, PARAMETERS["RSI_period"])

#     buy_condition  = ema_short.iloc[-1] > ema_long.iloc[-1] and rsi.iloc[-1] < 65
#     sell_condition = ema_short.iloc[-1] < ema_long.iloc[-1] and rsi.iloc[-1] > 35

#     positions = []
#     try:
#         resp = safe_get(f"{base_url_slave}/positions", timeout=10)
#         if resp is None:
#             log(f"❌ Trader {trader.id} slave offline")
#             return
#         resp.raise_for_status()
#         positions = resp.json()
#     except Exception as e:
#         log(f"❌ Errore posizioni slave trader {trader.id}: {e}")
#         return

#     def slave_has_position(order_type=None):
#         return any(
#             p.get("symbol") == symbol and (order_type is None or p.get("type") == order_type)
#             for p in positions
#         )

#     # =========================
#     # BUY
#     # =========================
#     if buy_condition:
#         current_signal = "BUY"
#         if not slave_has_position(0) and not slave_has_position(1):
#             send_buy_to_slave()
#         previous_signal = current_signal
#         return

#     # =========================
#     # SELL
#     # =========================
#     if sell_condition:
#         current_signal = "SELL"
#         if not slave_has_position(1) and not slave_has_position(0):
#             send_sell_to_slave()
#         previous_signal = current_signal
#         return

#     # =========================
#     # HOLD
#     # =========================
#     current_signal = "HOLD"
#     previous_signal = current_signal
#     log(f"[{now}] HOLD per trader {trader.id} ({symbol})")
