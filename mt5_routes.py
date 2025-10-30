from datetime import datetime
import os
import MetaTrader5 as mt5
import logging
from fastapi import APIRouter, HTTPException, Query
from models import (
    LoginRequest, LoginResponse, BuyRequest, SellRequest, CloseRequest,
    GetLastCandleRequest, GetLastDealsHistoryRequest, DealsAllResponse,
    ServerCheckRequest, TraderServersUpdate
)

router = APIRouter()

# --- LOGIN ---
@router.post("/login", response_model=LoginResponse)
def login(request: LoginRequest):
    if not mt5.initialize(login=int(request.login), password=request.password, server=request.server):
        logging.error(f"MT5 login failed: {mt5.last_error()}")
        raise HTTPException(status_code=400, detail="MT5 login failed")
    info = mt5.account_info()
    return {"login": info.login, "server": info.server, "balance": info.balance}

# --- ACCOUNT INFO & MARGIN ---
@router.get("/account/info")
def account_info():
    info = mt5.account_info()
    return info._asdict() if info else {"error": mt5.last_error()}

@router.get("/account/margin")
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


# --- SYMBOLS ---
@router.get("/symbols/tradable")
def symbols_tradable():
    symbols = mt5.symbols_get()
    if not symbols:
        return {"error": mt5.last_error()}

    result = []
    for s in symbols:
        result.append({
            "symbol": s.name,
            "tradable": s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL,
            "selected": s.select,
            "currency_base": s.currency_base,
            "currency_profit": s.currency_profit,
            "spread": s.spread,
            "point": s.point
        })
    return result

# --verfica la tradibilità di un simbolo
@router.get("/diagnostic/{symbol}")
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


# --- BUY ORDER ---
@router.post("/order/buy")
def buy(request: BuyRequest):
    symbol = request.symbol
    lot = request.volume
    price = mt5.symbol_info_tick(symbol).ask
    order = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "deviation": 10,
        "magic": 42,
        "comment": "API Buy Order",
    })
    if order.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(status_code=400, detail=f"Order failed: {order.comment}")
    return {"status": "ok", "order": order._asdict()}

# --- CLOSE ALL ORDERS ---
@router.post("/orders/close_all")
def close_all():
    positions = mt5.positions_get()
    if not positions:
        return {"message": "No open positions"}
    for pos in positions:
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price": mt5.symbol_info_tick(pos.symbol).bid if pos.type == 0 else mt5.symbol_info_tick(pos.symbol).ask,
            "deviation": 20,
            "magic": 42,
            "comment": "API Close All"
        }
        result = mt5.order_send(close_request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logging.error(f"Failed to close {pos.symbol}: {result.comment}")
    return {"status": "ok"}



@router.post("/check-server")
async def check_server(data: ServerCheckRequest):
    # Verifica che il path esista
    print("Ricevuto dal client:", data.dict())

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

@router.post("/order/sell")
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

# --- ORDERS HISTORY : NON ANCORA ESEGUITI !---
@router.get("/orders/history", summary="Storico ordini", description="Restituisce lo storico ordini tra due date.")
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
@router.post("/order/modify", summary="Modifica posizione", description="Modifica StopLoss e TakeProfit di una posizione aperta.")
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
