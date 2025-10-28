import logging
import os
# import time
# import traceback
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fastapi_utils.tasks import repeat_every

from models import LoginRequest, LoginResponse, BuyRequest, \
ServerCheckRequest, UserResponse,GetLastCandleRequest,SellRequest, \
CloseRequest,GetLastDealsHistoryRequest,DealsAllResponse

from fastapi import FastAPI, Query,HTTPException, Request
import MetaTrader5 as mt5
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

# from db import get_connection   # <--- IMPORT da db.py
from db import *
from db import router as db_router

import bcrypt


# --- LOGGING ---
log_file_path = "./fxscript.log"
logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logging.info("Starting API")

# --- FASTAPI APP ---
app = FastAPI()

app.include_router(db_router)


# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],  # il tuo frontend Angular
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- HELPER ---
DEAL_TYPES = {
    mt5.DEAL_TYPE_BUY: "BUY",
    mt5.DEAL_TYPE_SELL: "SELL",
    mt5.DEAL_TYPE_BALANCE: "BALANCE",
    mt5.DEAL_TYPE_CREDIT: "CREDIT",
    mt5.DEAL_TYPE_CHARGE: "CHARGE",
    mt5.DEAL_TYPE_COMMISSION: "COMMISSION",
    # mt5.DEAL_TYPE_FEE: "FEE",
    # mt5.DEAL_TYPE_INTEREST: "INTEREST",
    # mt5.DEAL_TYPE_SWAP: "SWAP",
    # mt5.DEAL_TYPE_CLOSE_BY: "CLOSE_BY"
}

def get_trade_mode_description(mode):
    modes = {
        0: "SYMBOL_TRADE_MODE_DISABLED - Trading disabilitato",
        1: "SYMBOL_TRADE_MODE_LONGONLY - Solo buy permesso",
        2: "SYMBOL_TRADE_MODE_SHORTONLY - Solo sell permesso", 
        3: "SYMBOL_TRADE_MODE_CLOSEONLY - Solo chiusura posizioni",
        4: "SYMBOL_TRADE_MODE_FULL - Trading completo abilitato"
    }
    return modes.get(mode, f"Unknown mode: {mode}")

def close_all(symbol, magic, deviation):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return []
    cur_tick = mt5.symbol_info_tick(symbol)
    res = []
    for p in positions:
        if p.magic != magic:
            continue
        if p.type == mt5.ORDER_TYPE_BUY:
            price = cur_tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        elif p.type == mt5.ORDER_TYPE_SELL:
            price = cur_tick.ask
            order_type = mt5.ORDER_TYPE_BUY
        else:
            continue
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": p.volume,
            "type": order_type,
            "position": p.ticket,
            "price": price,
            "deviation": deviation,
            "magic": p.magic,
            "comment": "python script close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res.append(mt5.order_send(request))
    return res

# --- STARTUP EVENT ---
# @app.on_event("startup") --- originale con il login
# def startup_event():
    path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if not os.path.exists(path):
        logging.error(f"MetaTrader5 path not found: {path}")
        raise FileNotFoundError(f"{path} does not exist")
    
    # login = os.environ.get("ACCOUNT")
    # password = os.environ.get("PASSWORD")
    # server = os.environ.get("SERVER")

    # --- CREDENZIALI HARD-CODED ---
    login = "959911"
    password = "Qpnldan1@1"
    server = "VTMarkets-Demo"
    if not mt5.initialize(path, login=int(login), password=str(password), server=server):
        logging.error(f"MT5 initialize failed: {mt5.last_error()}")
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    logging.info("MT5 initialized successfully")

# versione che non si logga
@app.on_event("startup")
def startup_event():
    logging.info("Avvio dell'app FastAPI...")
    
    # Chiude eventuali sessioni MT5 aperte
    mt5.shutdown()
    
    try:
        # Inizializza MT5 con timeout
        if not mt5.initialize(
            path=MT5_PATH, # type: ignore
            login=int(MT5_LOGIN), # type: ignore
            password=str(MT5_PASSWORD), # type: ignore
            server=MT5_SERVER, # type: ignore
            timeout=5000  # 5 secondi
        ):
            logging.error(f"MT5 initialize failed: {mt5.last_error()}")
            logging.warning("MT5 non inizializzato, alcune funzionalità potrebbero non funzionare.")
        else:
            logging.info("MT5 initialized successfully")
    except Exception as e:
        logging.exception(f"Errore durante l'inizializzazione MT5: {e}")
        logging.warning("MT5 non inizializzato, alcune funzionalità potrebbero non funzionare.")
# --- ENDPOINTS ---
@app.get("/healthz")
def healthz():
    info = mt5.account_info()
    if info and "login" in info._asdict():
        return {"status": "ok"}
    return {"status": "error", "details": mt5.last_error()}

@app.get("/")
def read_root():
    try:
        return mt5.account_info()._asdict()
    except:
        return {"error": mt5.last_error()}

@app.post("/candle/last")
def candle_last(inp: GetLastCandleRequest):
    tf_dic = {v.replace("TIMEFRAME_", "")[1:] + v.replace("TIMEFRAME_", "")[0].lower(): getattr(mt5, v)
              for v in dir(mt5) if v.startswith("TIMEFRAME_")}
    timeframe = tf_dic.get(inp.timeframe, None)
    if not timeframe:
        return {"error": f"Invalid timeframe {inp.timeframe}"}
    rates = mt5.copy_rates_from_pos(inp.symbol, timeframe, inp.start, inp.count)
    return rates.tolist() if rates is not None else {"error": mt5.last_error()}

# trade aperto e ancora attivo
@app.get("/positions/open")
def positions_open():
    positions = mt5.positions_get()
    return [p._asdict() for p in positions] if positions else {"error": mt5.last_error()}

@app.get("/symbols/all")
def symbols_all():
    symbols = mt5.symbols_get()
    return [s.name for s in symbols] if symbols else {"error": mt5.last_error()}
# lista con simboli + tradabilità
@app.get("/symbols/tradable_fast")
def symbols_tradable_fast():
    symbols = mt5.symbols_get()
    if not symbols:
        return {"error": mt5.last_error()}

    result = []
    for s in symbols:
        result.append({
            "symbol": s.name,
            "tradable": s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL,
            "selected": s.select,  # True se il simbolo è visibile nel Market Watch
            "currency_base": s.currency_base,
            "currency_profit": s.currency_profit,
            "spread": s.spread,
            "point": s.point
        })
    return result

# --- ORDERS ---
@app.post("/order/buy")
def order_buy(req: BuyRequest):
    tick = mt5.symbol_info_tick(req.symbol)
    if not tick:
        return {"error": mt5.last_error()}
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": req.symbol,
        "volume": req.lot,
        "type": mt5.ORDER_TYPE_BUY,
        "price": tick.ask,
        "sl": tick.ask - req.sl_point * mt5.symbol_info(req.symbol).point,
        "tp": tick.ask + req.tp_point * mt5.symbol_info(req.symbol).point,
        "magic": req.magic,
        "comment": req.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    return result._asdict() if result else {"error": mt5.last_error()}

@app.post("/order/sell")
def order_sell(req: SellRequest):
    tick = mt5.symbol_info_tick(req.symbol)
    if not tick:
        return {"error": mt5.last_error()}
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": req.symbol,
        "volume": req.lot,
        "type": mt5.ORDER_TYPE_SELL,
        "price": tick.bid,
        "sl": tick.bid + req.sl_point * mt5.symbol_info(req.symbol).point,
        "tp": tick.bid - req.tp_point * mt5.symbol_info(req.symbol).point,
        "deviation": req.deviation,
        "magic": req.magic,
        "comment": req.comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    return result._asdict() if result else {"error": mt5.last_error()}

@app.post("/order/close")
def order_close(req: CloseRequest):
    res = close_all(req.symbol, req.magic, req.deviation)
    return [r._asdict() if r else {"error": mt5.last_error()} for r in res]
# --- TICKS ---
@app.get("/ticks/last/{symbol}", summary="Ultimo tick", description="Restituisce l'ultimo tick (bid/ask/time) del simbolo.")
def tick_last(symbol: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        return tick._asdict()
    return {"error": mt5.last_error()}


@app.get("/ticks/range/{symbol}", summary="Ticks in intervallo", description="Restituisce i tick di un simbolo tra due date.")
def ticks_range(
    symbol: str,
    from_date: str = Query(..., description="Data inizio formato YYYY-MM-DD HH:MM:SS"),
    to_date: str = Query(..., description="Data fine formato YYYY-MM-DD HH:MM:SS"),
):
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d %H:%M:%S")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"error": "Formato data non valido. Usa YYYY-MM-DD HH:MM:SS"}

    ticks = mt5.copy_ticks_range(symbol, from_dt, to_dt, mt5.COPY_TICKS_ALL)
    if ticks is None:
        return {"error": mt5.last_error()}
    return ticks.tolist()


# --- ORDERS HISTORY : NON ANCORA ESEGUITI !---
@app.get("/orders/history", summary="Storico ordini", description="Restituisce lo storico ordini tra due date.")
def orders_history(
    from_date: str = Query("2024-01-01 00:00:00", description="Data inizio"),
    to_date: str = Query(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), description="Data fine"),
):
    try:
        from_dt = datetime.strptime(from_date, "%Y-%m-%d %H:%M:%S")
        to_dt = datetime.strptime(to_date, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return {"error": "Formato data non valido. Usa YYYY-MM-DD HH:MM:SS"}

    orders = mt5.history_orders_get(from_dt, to_dt)
    if orders is None:
        return {"error": mt5.last_error()}
    return [o._asdict() for o in orders]


# --- ORDER MODIFY ---
@app.post("/order/modify", summary="Modifica posizione", description="Modifica StopLoss e TakeProfit di una posizione aperta.")
def order_modify(
    symbol: str,
    ticket: int,
    new_sl: float,
    new_tp: float,
):
    position = mt5.positions_get(ticket=ticket)
    if not position:
        return {"error": f"Posizione {ticket} non trovata."}
    pos = position[0]
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": symbol,
        "position": ticket,
        "sl": new_sl,
        "tp": new_tp,
        "magic": pos.magic,
        "comment": "modify SL/TP",
    }
    result = mt5.order_send(request)
    return result._asdict() if result else {"error": mt5.last_error()}


# --- SYMBOL SELECT ---
@app.post("/symbol/select/{symbol}", summary="Seleziona simbolo", description="Attiva o disattiva un simbolo nel Market Watch.")
def symbol_select(symbol: str, enable: bool = True):
    res = mt5.symbol_select(symbol, enable)
    if res:
        return {"symbol": symbol, "selected": enable}
    return {"error": mt5.last_error()}


# --- DEALS HISTORY =esecuzione reale---
@app.post("/deals/history")
def deals_history(req: GetLastDealsHistoryRequest):
    deals = mt5.history_deals_get(datetime.now() - timedelta(days=30), datetime.now(), req.symbol)
    if deals is None:
        return {"error": mt5.last_error()}
    data = [d._asdict() for d in deals]
    return DealsAllResponse(total=len(data), limit=len(data), offset=0, data=data)

# --- ORDERS PENDING ---
@app.get("/orders/pending")
def orders_pending():
    orders = mt5.orders_get()
    return [o._asdict() for o in orders] if orders else {"error": mt5.last_error()}

# --- ACCOUNT INFO & MARGIN ---
@app.get("/account/info")
def account_info():
    info = mt5.account_info()
    return info._asdict() if info else {"error": mt5.last_error()}

@app.get("/account/margin")
def account_margin():
    info = mt5.account_info()
    if info:
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "margin_level": info.margin_level,
        }
    return {"error": mt5.last_error()}

# --- SYMBOL INFO & STATUS ---
@app.get("/symbol/info/{symbol}")
def symbol_info(symbol: str):
    info = mt5.symbol_info(symbol)
    if info:
        return info._asdict()
    return {"error": mt5.last_error()}

@app.get("/symbol/trading_mode/{symbol}")
def symbol_trading_mode(symbol: str):
    info = mt5.symbol_info(symbol)
    if info:
        return {"mode": get_trade_mode_description(info.trade_mode)}
    return {"error": mt5.last_error()}

# --- CLOSE ALL POSITIONS BY MAGIC ---
@app.post("/positions/close_all")
def close_all_positions(req: CloseRequest):
    res = close_all(req.symbol, req.magic, req.deviation)
    return [r._asdict() if r else {"error": mt5.last_error()} for r in res]

# --verfica la tradibilità di un simbolo
@app.get("/diagnostic/{symbol}")
def diagnostic(symbol: str):
    info = {}

    # 1) Terminale inizializzato?
    info["initialized"] = mt5.initialize()

    # 2) Account info
    acc = mt5.account_info()
    info["account_connected"] = acc is not None
    if acc:
        info["trade_allowed"] = acc.trade_allowed
        info["trade_mode"] = acc.trade_mode
        info["margin_mode"] = acc.margin_mode

    # 3) Simbolo
    sym = mt5.symbol_info(symbol)
    info["symbol_exists"] = sym is not None
    if sym:
        info["symbol_tradable"] = sym.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL
        info["symbol_trade_mode"] = sym.trade_mode
        # Se non selezionato → selezioniamolo
        info["symbol_selected"] = sym.select if sym.select else mt5.symbol_select(symbol, True)

    # 4) Prova ORDER_CHECK (simulazione ordine)
    if sym and acc:
        tick = mt5.symbol_info_tick(symbol)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": 0.1,
            "type": mt5.ORDER_TYPE_BUY,
            "price": tick.ask if tick else 0,
            "sl": 0,
            "tp": 0,
            "deviation": 10,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        check = mt5.order_check(req)
        info["order_check"] = check._asdict() if check else mt5.last_error()

    return info

@app.get("/test_db")
def test_db():
    conn = get_connection()
    if not conn:
        return {"status": "error", "message": "Connessione fallita"}

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT NOW()")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return {"status": "success", "message": f"Connessione OK! Ora DB: {result[0]}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
@app.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    conn = get_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, username, password FROM users WHERE username=%s",
        (req.username,)
    )
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return LoginResponse(success=False, message="Invalid credentials", user=None)

    # Controllo password con bcrypt
    if bcrypt.checkpw(req.password.encode(), user["password"].encode()):
        # Aggiorna last_login
        cursor.execute(
            "UPDATE users SET last_login = NOW() WHERE id=%s",
            (user["id"],)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return LoginResponse(
            success=True,
            user=UserResponse(id=user["id"], username=user["username"]),
            message="Login successful"
        )

    cursor.close()
    conn.close()
    return LoginResponse(success=False, message="Invalid credentials", user=None)


@app.post("/check-server")
def check_server(data: ServerCheckRequest):
    # Verifica che il path esista
    if not os.path.exists(data.path):
        return {"status": "error", "message": f"Terminal not found at {data.path}"}

    # Chiude eventuale sessione precedente
    if mt5.initialize():
        mt5.shutdown()

    # Prova a inizializzare MT5
    connected = mt5.initialize(
        path=data.path,
        server=data.server,
        login=data.login,
        password=data.password,
        port=data.port
    )

    if connected:
        # Inizializzazione ok
        version = mt5.version()
        mt5.shutdown()
        return {
            "status": "success",
            "message": "Server reachable and login valid",
            "mt5_version": version
        }
    else:
        # Inizializzazione fallita
        error = mt5.last_error()
        return {"status": "error", "message": f"Cannot connect: {error}"}

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    print("=== VALIDATION ERROR ===")
    print("Body ricevuto:", body.decode())
    print("Errori:", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body": body.decode()}
    )

from fastapi_utils.tasks import repeat_every

@router.on_event("startup")
@repeat_every(seconds=10)  # ogni 10 secondi, puoi cambiare
def auto_copy_trading():
    """
    Replica automaticamente gli ordini dai master ai relativi slave.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        # 1. Recupera tutti i trader attivi che hanno slave
        cursor.execute("""
            SELECT t.id as master_id, t.master_server_id, t.slave_server_id
            FROM traders t
            WHERE t.is_active= 1
              AND t.slave_server_id IS NOT NULL
        """)
        masters = cursor.fetchall()

        for master in masters:
            master_id = master["master_id"]
            slave_id = master["slave_server_id"]

            # 2. Ordini aperti sul master
            cursor.execute("SELECT * FROM orders WHERE trader_id=%s AND status='open'", (master_id,))
            master_orders = cursor.fetchall()

            # 3. Replica sugli slave
            for order in master_orders:
                # Controlla se ordine già copiato per evitare duplicati
                cursor.execute("""
                    SELECT id FROM orders
                    WHERE trader_id=%s AND symbol=%s AND magic=%s AND status='open'
                """, (slave_id, order["symbol"], order["magic"]))
                if cursor.fetchone():
                    continue  # già copiato

                # Applica moltiplicatore dello slave se presente
                cursor.execute("SELECT moltiplicatore FROM traders WHERE id=%s", (slave_id,))
                slave_mult = cursor.fetchone()
                lot_multiplier = slave_mult["moltiplicatore"] if slave_mult else 1

                cursor.execute("""
                    INSERT INTO orders
                    (trader_id, symbol, lot, sl, tp, magic, comment, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'open')
                """, (
                    slave_id,
                    order["symbol"],
                    order["lot"] * lot_multiplier,
                    order["sl"],
                    order["tp"],
                    order["magic"],
                    f"Copy from trader {master_id}"
                ))

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        print("Errore copytrading automatico:", e)


# --- UVICORN RUN ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mt:app", host="127.0.0.1", port=8080, reload=True)
    # uvicorn.run("mt:app", host="127.0.0.1", port=8080)

    # lanciare con python mt.py, senza il reload=tru
