# mt5_api.py: sta su ogni SERVER su cui viene istanziato mt5
# from logging import log
from datetime import datetime
import os
import MetaTrader5 as mt5
import concurrent
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sys
import uvicorn

app = FastAPI(title="MT5 Local API")

class LoginRequest(BaseModel):
    login: int
    password: str
    server: str
    
class InitRequest(BaseModel):
    path: str
    
logs = []  # elenco dei messaggi di log

start_time = datetime.now()  
# funz messaggistica di log
def log(message: str):
        """Aggiunge un messaggio con timestamp relativo."""
        elapsed = (datetime.now() - start_time).total_seconds()
        timestamp = f"[+{elapsed:.1f}s]"
        logs.append(f"{timestamp} {message}")
        print(f"{timestamp} {message}")  # Mantieni anche la stampa in consol


@app.post("/init-mt5")
def init_mt5(req: InitRequest):
    """
    Inizializza (o re-inizializza) il terminale MT5 locale.
    Da chiamare via HTTP dal manager con:
        POST http://<ip_server>:<port>/init-mt5
        {
          "path": "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
        }
    """
    global CURRENT_PATH

    # Se già inizializzato, chiudi prima
    try:
        mt5.shutdown()
    except Exception:
        pass

    if not os.path.exists(req.path):
        raise HTTPException(status_code=400, detail=f"Percorso MT5 non trovato: {req.path}")

    ok = mt5.initialize(req.path)
    if not ok:
        err = mt5.last_error()
        raise HTTPException(status_code=500, detail=f"Fallita inizializzazione MT5: {err}")

    CURRENT_PATH = req.path
    version = ".".join(map(str, mt5.version()))
    print(f"✅ MT5 inizializzato da API al path {req.path} (versione {version})")

    return {"status": "ok", "message": f"MT5 inizializzato", "version": version, "path": req.path}

@app.on_event("startup")
def startup_event():
    """
    Evento di startup di FastAPI:
    - Recupera il percorso del terminale MT5 dai parametri da linea di comando (sys.argv)
    - Inizializza MetaTrader 5 con il path fornito
    - Solleva un errore se l'inizializzazione fallisce
    """

    import sys
    import MetaTrader5 as mt5


@app.on_event("shutdown")
def shutdown_event():
    mt5.shutdown()


@app.post("/login")
def login(req: LoginRequest):
    """
    Esegue il login MT5 con timeout protetto.
    Evita che il server resti bloccato se il terminale non risponde.
    """
    print(f"🟢 Tentativo login MT5: login={req.login}, server={req.server}")

    # 1️⃣ Verifica inizializzazione
    info = mt5.terminal_info()
    if info is None:
        raise HTTPException(status_code=500, detail="MT5 non inizializzato. Usa /init-mt5 prima del login.")

    # 2️⃣ Esegue login in thread con timeout (per evitare blocchi)
    def do_login():
        return mt5.login(req.login, req.password, req.server)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(do_login)
        try:
            success = future.result(timeout=10)  # 10 secondi di timeout
        except concurrent.futures.TimeoutError:
            raise HTTPException(status_code=504, detail="Timeout: il terminale MT5 non ha risposto al login entro 10s.")

    # 3️⃣ Controllo risultato login
    if not success:
        err = mt5.last_error()
        raise HTTPException(status_code=400, detail=f"Login fallito: {err}")

    info = mt5.account_info()
    balance = info.balance if info else None
    print(f"✅ Login MT5 riuscito: {req.login} - Balance: {balance}")

    return {"message": "✅ Login OK", "balance": balance}

@app.get("/health")
def health_check():
    """
    Health check endpoint:
    - Controlla se MT5 è inizializzato
    - Restituisce stato OK o errore
    """
    try:
        info = mt5.terminal_info()
        if info is None:
            return {"status": "error", "message": "MT5 non inizializzato"}

        # mt5.version() restituisce (major, minor, build, revision)
        version = mt5.version()
        version_str = ".".join(map(str, version))  # converte in stringa tipo "5.00.1234.0"

        return {"status": "ok", "mt5_version": version_str}
    except Exception as e:
        print("Errore INIT:", mt5.last_error())
        return {"status": "error", "message": str(e)}


@app.get("/positions")
def get_positions():
    positions = mt5.positions_get()
    if positions is None:
        raise HTTPException(status_code=400, detail="Cannot get positions")
    return [p._asdict() for p in positions]

@app.get("/symbol_info/{symbol}")
def get_symbol_info(symbol: str):
    info = mt5.symbol_info(symbol)
    if not info:
        return {"error": "symbol not found"}
    return {
        "name": info.name,
        "visible": info.visible,
        "trade_mode": info.trade_mode,
        "spread": info.spread,
        "point": info.point
    }

@app.post("/symbol_select")
def select_symbol(data: dict):
    symbol = data.get("symbol")
    if not symbol:
        return {"error": "missing symbol"}
    success = mt5.symbol_select(symbol, True)
    return {"symbol": symbol, "enabled": success}

@app.get("/symbol_tick/{symbol}")
def get_symbol_tick(symbol: str):
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return {"error": "no tick available"}
    return {"bid": tick.bid, "ask": tick.ask, "last": tick.last}

@app.get("/terminal_info")
def get_terminal_info():
    try:
        info = mt5.terminal_info()
        if info is None:
            raise HTTPException(status_code=500, detail="Impossibile ottenere terminal info")
        return info._asdict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@app.post("/close_order/{ticket}")
def close_order(ticket: int):
    """
    Chiude una posizione aperta sul conto MT5.
    Utilizzata dallo slave per chiudere posizioni che non ci sono più sul master.
    """
    log(f"🔹 Tentativo di chiusura ordine ticket {ticket}")

    # Recupera la posizione
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        log(f"❌ Posizione con ticket {ticket} non trovata")
        return {"error": f"❌ Posizione con ticket {ticket} non trovata."}

    pos = positions[0]
    symbol = pos.symbol
    lot = pos.volume
    profit = pos.profit  # profit corrente prima della chiusura


    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        log(f"❌ Impossibile leggere tick per {symbol}")
        return {"error": f"❌ Impossibile leggere tick per {symbol}"}

    # Determina tipo ordine inverso e prezzo per chiusura: la chiusura implica segno inverso
    if pos.type == mt5.ORDER_TYPE_BUY:
        price = tick.bid
        order_type = mt5.ORDER_TYPE_SELL
    elif pos.type == mt5.ORDER_TYPE_SELL:
        price = tick.ask
        order_type = mt5.ORDER_TYPE_BUY
    else:
        log(f"❌ Tipo ordine non riconosciuto: {pos.type}")
        return {"error": f"❌ Tipo ordine non riconosciuto: {pos.type}"}

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": 123456,
        "comment": "auto-close via API",
    }

    result = mt5.order_send(request)

    if result is None:
        log(f"❌ Nessuna risposta da MT5 per ticket {ticket}: {mt5.last_error()}")
        return {"error": "❌ Nessuna risposta da MT5", "details": mt5.last_error()}

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"❌ Chiusura ordine {ticket} fallita, retcode: {result.retcode}")
        return {"error": f"❌ Chiusura fallita", "retcode": result.retcode, "details": result._asdict()}

    log(f"✅ Ordine {ticket} chiuso correttamente: {symbol}, volume {lot}, prezzo {price}")
    return {
        "status": "✅ Ordine chiuso",
        "ticket_closed": ticket,
        "symbol": symbol,
        "volume": lot,
        "price": price,
        "profit": profit,         
        "retcode": result.retcode
    }


@app.post("/order")
def send_order(order: dict):
    print(f"📩 Ricevuto ordine dallo slave: {order}")

    # 🔹 Conversione dei tipi numerici
    order["volume"] = float(order.get("volume", 0))
    order["price"] = float(order.get("price", 0))
    order["sl"] = float(order.get("sl", 0)) if order.get("sl") else 0.0
    order["tp"] = float(order.get("tp", 0)) if order.get("tp") else 0.0

    # 🔹 Traduzione tipo ordine testuale → costante MT5
    order_type = order.get("type")
    if order_type == "buy":
        order["type"] = mt5.ORDER_TYPE_BUY
    elif order_type == "sell":
        order["type"] = mt5.ORDER_TYPE_SELL
    else:
        raise HTTPException(status_code=400, detail=f"Invalid order type: {order_type}")

    # 🔹 Aggiunge parametri mancanti richiesti da MT5
    order.setdefault("action", mt5.TRADE_ACTION_DEAL)
    order.setdefault("deviation", 10)
    order.setdefault("type_time", mt5.ORDER_TIME_GTC)
    order.setdefault("magic", 123456)

    print(f"🚀 Inviando ordine a MetaTrader5: {order}")
    result = mt5.order_send(order)

    if result is None:
        raise HTTPException(status_code=500, detail=f"MT5 order_send() returned None")

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"❌ Errore invio ordine: {result}")
        raise HTTPException(status_code=400, detail=f"Trade failed: {result.comment}")

    print(f"✅ Ordine eseguito correttamente: {result}")
    return {"message": "✅ Order sent", "result": result._asdict()}


# -----------------------
# BLOCCO DI AVVIO
# -----------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python mt5_api.py <MT5_PATH> <HOST:PORT>")
        sys.exit(1)
    # path di MT5
    mt5_path = sys.argv[1] 
    # host+porta
    host_port = sys.argv[2]
    host, port_str = host_port.split(":")
    port = int(port_str)

    if not os.path.exists(mt5_path):
        print(f"❌ Terminale MT5 non trovato: {mt5_path}")
        sys.exit(1)

    if not mt5.initialize(mt5_path):
        err = mt5.last_error()
        print(f"❌ Fallita inizializzazione MT5: {err}")
        sys.exit(1)

    print(f"✅ MT5 inizializzato correttamente da {mt5_path}")
    print(f"🟢 Avvio FastAPI su {host}:{port}")

    uvicorn.run(app, host=host, port=port)

