import os
import MetaTrader5 as mt5
import logging
from fastapi import APIRouter, HTTPException
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
