from datetime import datetime
from pydantic import BaseModel
import requests
from logger import  logs
from fastapi import APIRouter, HTTPException, Request
from models import (
    LoginRequest, LoginResponse, BuyRequest, SellRequest, CloseRequest,
    GetLastCandleRequest, GetLastDealsHistoryRequest, DealsAllResponse,
    ServerCheckRequest, ServerRequest, TraderServersUpdate
)

router = APIRouter()

# --- LOGIN ---
# @router.post("/login", response_model=LoginResponse)
# def login(request: LoginRequest):
#     if not mt5.initialize(login=int(request.login), password=request.password, server=request.server):
#         logging.error(f"MT5 login failed: {mt5.last_error()}")
#         raise HTTPException(status_code=400, detail="MT5 login failed")
#     info = mt5.account_info()
#     return {"login": info.login, "server": info.server, "balance": info.balance}

# --- ACCOUNT INFO & MARGIN ---
# @router.get("/account/info")
# def account_info():
#     info = mt5.account_info()
#     return info._asdict() if info else {"error": mt5.last_error()}

# @router.get("/account/margin")
# def account_margin():
#     info = mt5.account_info()
#     if info:
#         return {
#             "balance": info.balance,
#             "equity": info.equity,
#             "margin": info.margin,
#             "margin_level": info.margin_level,
#         }
#     return {"error": mt5.last_error()}


# --- SYMBOLS ---
# @router.get("/symbols/tradable")
# def symbols_tradable():
#     symbols = mt5.symbols_get()
#     if not symbols:
#         return {"error": mt5.last_error()}

#     result = []
#     for s in symbols:
#         result.append({
#             "symbol": s.name,
#             "tradable": s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL,
#             "selected": s.select,
#             "currency_base": s.currency_base,
#             "currency_profit": s.currency_profit,
#             "spread": s.spread,
#             "point": s.point
#         })
#     return result

# --verfica la tradibilità di un simbolo
# @router.get("/diagnostic/{symbol}")
# def diagnostic(symbol: str):
#     info = {}

#     # 1) Terminale inizializzato?
#     info["initialized"] = mt5.initialize()

#     # 2) Account info
#     acc = mt5.account_info()
#     info["account_connected"] = acc is not None
#     if acc:
#         info["trade_allowed"] = acc.trade_allowed
#         info["trade_mode"] = acc.trade_mode
#         info["margin_mode"] = acc.margin_mode

#     # 3) Simbolo
#     sym = mt5.symbol_info(symbol)
#     info["symbol_exists"] = sym is not None
#     if sym:
#         info["symbol_tradable"] = sym.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL
#         info["symbol_trade_mode"] = sym.trade_mode
#         # Se non selezionato → selezioniamolo
#         info["symbol_selected"] = sym.select if sym.select else mt5.symbol_select(symbol, True)

#     # 4) Prova ORDER_CHECK (simulazione ordine)
#     if sym and acc:
#         tick = mt5.symbol_info_tick(symbol)
#         req = {
#             "action": mt5.TRADE_ACTION_DEAL,
#             "symbol": symbol,
#             "volume": 0.1,
#             "type": mt5.ORDER_TYPE_BUY,
#             "price": tick.ask if tick else 0,
#             "sl": 0,
#             "tp": 0,
#             "deviation": 10,
#             "type_time": mt5.ORDER_TIME_GTC,
#             "type_": mt5.ORDER_FILLING_IOC,
#         }
#         check = mt5.order_check(req)
#         info["order_check"] = check._asdict() if check else mt5.last_error()

#     return info


# --- BUY ORDER ---
# @router.post("/order/buy")
# def buy(request: BuyRequest):
#     symbol = request.symbol
#     lot = request.volume
#     price = mt5.symbol_info_tick(symbol).ask
#     order = mt5.order_send({
#         "action": mt5.TRADE_ACTION_DEAL,
#         "symbol": symbol,
#         "volume": lot,
#         "type": mt5.ORDER_TYPE_BUY,
#         "price": price,
#         "deviation": 10,
#         "magic": 42,
#         "comment": "API Buy Order",
#     })
#     if order.retcode != mt5.TRADE_RETCODE_DONE:
#         raise HTTPException(status_code=400, detail=f"Order failed: {order.comment}")
#     return {"status": "ok", "order": order._asdict()}

# --- CLOSE ALL ORDERS ---
# @router.post("/orders/close_all")
# def close_all():
#     positions = mt5.positions_get()
#     if not positions:
#         return {"message": "No open positions"}
#     for pos in positions:
#         close_request = {
#             "action": mt5.TRADE_ACTION_DEAL,
#             "symbol": pos.symbol,
#             "volume": pos.volume,
#             "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
#             "position": pos.ticket,
#             "price": mt5.symbol_info_tick(pos.symbol).bid if pos.type == 0 else mt5.symbol_info_tick(pos.symbol).ask,
#             "deviation": 20,
#             "magic": 42,
#             "comment": "API Close All"
#         }
#         result = mt5.order_send(close_request)
#         if result.retcode != mt5.TRADE_RETCODE_DONE:
#             logging.error(f"Failed to close {pos.symbol}: {result.comment}")
#     return {"status": "ok"}



@router.post("/check-server")
async def manager_check_server(payload: dict):
    # L'host e port arrivano da Angular
    agent_url = f"http://{payload['host']}:{payload['port']}/check-server"
    # Chiamata all'agente mt5_api (che abbiamo memorizzato prima)
    r = requests.post(agent_url, json={}, timeout=5) 
    return r.json()

# @router.post("/order/sell")
# def order_sell(req: SellRequest):
#     tick = mt5.symbol_info_tick(req.symbol)
#     if not tick:
#         return {"error": mt5.last_error()}
#     request = {
#         "action": mt5.TRADE_ACTION_DEAL,
#         "symbol": req.symbol,
#         "volume": req.lot,
#         "type": mt5.ORDER_TYPE_SELL,
#         "price": tick.bid,
#         "sl": tick.bid + req.sl_point * mt5.symbol_info(req.symbol).point,
#         "tp": tick.bid - req.tp_point * mt5.symbol_info(req.symbol).point,
#         "deviation": req.deviation,
#         "magic": req.magic,
#         "comment": req.comment,
#         "type_time": mt5.ORDER_TIME_GTC,
#         "type_filling": mt5.ORDER_FILLING_IOC,
#     }
#     result = mt5.order_send(request)
#     return result._asdict() if result else {"error": mt5.last_error()}

# --- ORDERS HISTORY : NON ANCORA ESEGUITI !---
# @router.get("/orders/history", summary="Storico ordini", description="Restituisce lo storico ordini tra due date.")
# def orders_history(
#     from_date: str = Query("2024-01-01 00:00:00", description="Data inizio"),
#     to_date: str = Query(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), description="Data fine"),
# ):
#     try:
#         from_dt = datetime.strptime(from_date, "%Y-%m-%d %H:%M:%S")
#         to_dt = datetime.strptime(to_date, "%Y-%m-%d %H:%M:%S")
#     except ValueError:
#         return {"error": "Formato data non valido. Usa YYYY-MM-DD HH:MM:SS"}

#     orders = mt5.history_orders_get(from_dt, to_dt)
#     if orders is None:
#         return {"error": mt5.last_error()}
#     return [o._asdict() for o in orders]

# @router.post("/start_server")
# async def start_server(server: ServerRequest):
#     """
#     Avvia un terminale MetaTrader 5 (mt5_api\main.py) per il server indicato su host:port.
#     """
#     print(f"🚀 Avvio server {server.server} ({server.platform})...")

#     # 💡 PASSO CRUCIALE: Ottieni il percorso dell'interprete Python corrente (quello in venv)
#     python_executable = sys.executable
    
#     if not os.path.exists(server.path):
#         raise HTTPException(status_code=400, detail=f"Terminal not found at {server.path}")

#     # host e port definiti nel record server
#     host = server.ip       # es. "192.168.1.208"
#     port = server.port     # es. 8084
#     host_port = f"{host}:{port}"

#     try:
#         # Lancia nuova istanza MT5 API su host:port
#         # subprocess.Popen(
#         #     ["python", "mt5_api\main.py", server.path, host_port],
#         #     shell=False
#         # )

#         # Lancia nuova istanza MT5 API su host:port
#         # subprocess.Popen(
#         #     [python_executable, "mt5_api\main.py", server.path, host_port],
#         #     shell=False
#         # )
#         # print(f"✅ Terminal avviato da {server.path} su {host_port}")

#         # Lancia nuova istanza MT5 API su host:port, il path è di MT5
#         subprocess.Popen(
#             [python_executable, "main.py", server.path, host_port],
#             shell=False
#         )
#         print(f"✅ Terminal avviato da {server.path} su {host_port}")

#         return {
#             "status": "success",
#             "message": f"Terminal started for {server.server} on {host_port}",
#             "path": server.path,
#             "host": host,
#             "port": port
#         }

#     except Exception as e:
#         print(f"❌ Errore avvio terminal: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

class ServerRequest(BaseModel):
    server: str
    platform: str
    ip: str
    port: int
    path: str  # path completo al terminale MT5
    user: str     
    pwd: str


@router.post("/start_server")
async def start_server(server: ServerRequest):
    """
    Ordina all'agente remoto di avviare MT5 e lo inizializza.
    """
    import requests
    from fastapi import HTTPException

    # MAX_WAIT = 90  # secondi massimi di attesa
    # SLEEP_INTERVAL = 2  # intervallo tra i poll


    print(f"🚀 Richiesta avvio terminale MT5 su agente {server.ip}:{server.port}")

    # URL dell'agente remoto/locale
    agent_url_start = f"http://{server.ip}:{server.port}/start_mt5"
    agent_url_init = f"http://{server.ip}:{server.port}/init-mt5"

    agent_url_login = f"http://{server.ip}:{server.port}/login"


    # Payload per start_mt5
    payload_start = {
        "path": server.path
    }

    # Payload per init-mt5
    payload_init = {
        "path": server.path
    }

    payload_login = {
        "login": server.user,
        "password": server.pwd,
        "server": server.server
    }

    try:
        # 1️⃣ Avvia MT5 tramite agente (che è già avviato)
        response = requests.post(agent_url_start, json=payload_start, timeout=120)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Errore avvio MT5 agente: {response.text}")
        print(f"✅ MT5 avviato sul server remoto: {response.json()}")

        
        # 2️⃣ Inizializza MT5 (tramite agente già avviato)
        response_init = requests.post(agent_url_init, json=payload_init, timeout=30)
        if response_init.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Errore init MT5 agente: {response_init.text}")
        print(f"✅ MT5 inizializzato sul server remoto: {response_init.json()}")

        # 3️⃣ Login MT5 ()
        response_login = requests.post(agent_url_login, json=payload_login, timeout=30)
        if response_login.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Errore login MT5: {response_login.text}")
        print(f"🔐 Login MT5 effettuato: {response_login.json()}")


        return {
            "status": "success",
            "agent": f"{server.ip}:{server.port}",
            "message": "MT5 avviato e inizializzato tramite agente remoto",
            "server_path": server.path,
            "init_response": response_init.json()
        }

    except Exception as e:
        print(f"❌ Errore comunicazione agente remoto: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# @router.post("/webhook/open_position")
# async def open_position(req: Request):
#     data = await req.json()
#     print("📩 Nuova posizione ricevuta:", data)
#     # Qui puoi inserire nel DB, loggare o avviare il processo di copia
#     return {"status": "ok"}
#     # Esempio: chiama la funzione di copia (definita nel tuo db.py)
    