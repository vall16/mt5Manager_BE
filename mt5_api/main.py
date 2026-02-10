# mt5_api.py: sta su ogni SERVER su cui viene istanziato mt5
from contextlib import asynccontextmanager
from datetime import datetime
import os
import subprocess
# importante! gira solo in Windows !
import MetaTrader5 as mt5
# importante! gira solo in Windows !
import concurrent
import subprocess
import pandas as pd
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sys
import uvicorn
from logger import log, logs
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="MT5_API")

# Permette ad Angular in sviluppo di chiamare questa API
origins = ["http://localhost:4200", "http://127.0.0.1:4200"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    login: int
    password: str
    server: str
    
class InitRequest(BaseModel):
    path: str
    
class ServerCheckRequest(BaseModel):
    host: str
    port: int

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
    log(f"✅ MT5 inizializzato da API al path {req.path} (versione {version})")

    return {"status": "ok", "message": f"MT5 inizializzato", "version": version, "path": req.path}

# lancia l'exe locale
@app.post("/start_mt5")
def start_mt5(req: dict):
    path = req.get("path")

    print(f"▶️ Avvio MT5 da agente locale: {path}")

    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"MT5 non trovato: {path}")

    try:
        subprocess.Popen([path])
        print("👍 MT5 avviato")
        return {"status": "started", "path": path}

    except Exception as e:
        print(f"❌ Errore avvio MT5: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event handler:
    - Startup: inizializza MT5 se specificato nei parametri CLI
    - Shutdown: chiude MT5
    """
    log("🟢 API avviata, inizio lifespan")

    # Startup
    if len(sys.argv) >= 2:
        mt5_path = sys.argv[1]
        if os.path.exists(mt5_path):
            if not mt5.initialize(mt5_path):
                err = mt5.last_error()
                log(f"❌ Fallita inizializzazione MT5: {err}")
            else:
                CURRENT_PATH = mt5_path
                version = ".".join(map(str, mt5.version()))
                log(f"✅ MT5 inizializzato da lifespan al path {mt5_path} (versione {version})")
        else:
            log(f"❌ MT5 path non trovato: {mt5_path}")
    else:
        log("⚠️ Nessun MT5 path fornito come parametro, skip inizializzazione MT5")

    yield  # qui FastAPI serve le richieste

    # Shutdown
    try:
        mt5.shutdown()
        log("🛑 MT5 chiuso correttamente al shutdown")
    except Exception as e:
        log(f"⚠️ Errore durante shutdown MT5: {e}")



@app.post("/login")
def login(req: LoginRequest):
    """
    Esegue il login MT5 con timeout protetto.
    Evita che il server resti bloccato se il terminale non risponde.
    """
    log(f"🟢 Tentativo login MT5: login={req.login}, server={req.server}")

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
    log(f"✅ Login MT5 riuscito: {req.login} - Balance: {balance}")

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
        log("Errore INIT:", mt5.last_error())
        return {"status": "error", "message": str(e)}


@app.get("/positions")
def get_positions():
    positions = mt5.positions_get()
    if positions is None:
        # Logga ma ritorna lista vuota
        log("⚠️ Impossibile ottenere posizioni da MT5. Probabile mancanza di connessione")
        return []  # invece di sollevare HTTPException
    return [p._asdict() for p in positions]


@app.get("/symbols/active")
def get_active_symbols():
    """
    Restituisce SOLO i simboli:
    - già presenti nel Market Watch (selected=True)
    - tradabili (trade_mode = FULL)
    NON modifica la visibilità dei simboli.
    """
    info = mt5.terminal_info()
    if info is None:
        raise HTTPException(status_code=500, detail="MT5 non inizializzato. Prima usa /init-mt5.")

    symbols = mt5.symbols_get()
    if symbols is None:
        raise HTTPException(status_code=500, detail=f"symbols_get() failed: {mt5.last_error()}")

    active_symbols = []

    for s in symbols:
        if s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL and s.select:  # ← GIÀ visibile
            active_symbols.append({
                "symbol": s.name,
                "base": s.currency_base,
                "profit": s.currency_profit,
                "spread": s.spread,
                "digits": s.digits,
                "point": s.point
            })

    active_symbols.sort(key=lambda x: x["symbol"])

    return {
        "count": len(active_symbols),
        "symbols": active_symbols
    }



@app.get("/symbols/MWactive")
def get_active_symbols():
    """
    Restituisce tutti i simboli attivi (trade_mode = FULL).
    Li abilita nel Market Watch.
    Perfetto per dropdown lato frontend.
    """
    info = mt5.terminal_info()
    if info is None:
        raise HTTPException(status_code=500, detail="MT5 non inizializzato. Prima usa /init-mt5.")

    symbols = mt5.symbols_get()
    if symbols is None:
        raise HTTPException(status_code=500, detail=f"symbols_get() failed: {mt5.last_error()}")

    active_symbols = []

    for s in symbols:
        if s.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL:
            # assicura visibilità nel Market Watch
            mt5.symbol_select(s.name, True)

            active_symbols.append({
                "symbol": s.name,
                "spread": s.spread,
                "digits": s.digits,
                "point": s.point
            })

    active_symbols.sort(key=lambda x: x["symbol"])  # ordinati alfabeticamente

    return {
        "count": len(active_symbols),
        "symbols": active_symbols
    }


# interroga MetaTrader per conoscere le impostazioni "fisse" di quel simbolo
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

# serve a "accendere" o "spegnere" un simbolo all'interno della finestra Market Watch
@app.post("/symbol_select")
def select_symbol(data: dict):
    symbol = data.get("symbol")
    if not symbol:
        return {"error": "missing symbol"}
    success = mt5.symbol_select(symbol, True)
    return {"symbol": symbol, "enabled": success}

# Chiede l'ultimo "tick" disponibile per quel simbolo. 
# Un tick rappresenta l'aggiornamento più recente del prezzo sul mercato.
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



@app.post("/close_order_by_symbol")
def close_order_by_symbol(payload: dict):
    """
    Chiude tutte le posizioni aperte per un dato simbolo.
    Payload: {"symbol": "XAUUSD"}
    """
    symbol = payload.get("symbol")
    if not symbol:
        raise HTTPException(status_code=400, detail="Missing symbol in payload")

    # Recupera tutte le posizioni aperte per il simbolo
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        log(f"❌ Nessuna posizione aperta per {symbol}")
        return {"status": "none", "message": f"Nessuna posizione aperta per {symbol}"}

    closed = []
    errors = []

    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        if not tick:
            errors.append({"ticket": pos.ticket, "error": "No tick available"})
            continue

        # Determina tipo ordine inverso per chiusura
        if pos.type == mt5.ORDER_TYPE_BUY:
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        elif pos.type == mt5.ORDER_TYPE_SELL:
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY
        else:
            errors.append({"ticket": pos.ticket, "error": f"Unknown type {pos.type}"})
            continue

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": 123456,
            "comment": "auto-close by symbol"
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            errors.append({"ticket": pos.ticket, "result": result._asdict() if result else None})
        else:
            closed.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "price": price,
                "retcode": result.retcode
            })
            log(f"✅ Ordine chiuso: ticket={pos.ticket}, symbol={pos.symbol}, volume={pos.volume}")

    return {"closed": closed, "errors": errors}


# crea un ordine
@app.post("/order")
def send_order(order: dict):
    log(f"📩 Ricevuto ordine dallo slave: {order}")

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
    
    # gestione filling ! -------------------------------------
    # HARD CODED IOC per ICMARKETS ... da impostare solo se ICMARKETS, altrimenti nulla
    broker = (order.get("broker") or "").lower()

    if "icmarkets" in broker:
        order["type_filling"] = 1  # IOC (numero secco, zero rischi)

    # gestione filling ! -------------------------------------

    # 🔹 Aggiunge parametri mancanti richiesti da MT5
    order.setdefault("action", mt5.TRADE_ACTION_DEAL)
    order.setdefault("deviation", 10)
    order.setdefault("type_time", mt5.ORDER_TIME_GTC)
    order.setdefault("magic", 123456)

    log(f"🚀 Inviando ordine a MetaTrader5: {order}")
    result = mt5.order_send(order)

    if result is None:
        err = mt5.last_error()
        log("❌ MT5 order_send() failed:", err)
        raise HTTPException(
        status_code=500,
        detail=f"MT5 order_send() returned None, error: {err}"
    )
    else:
        log(f"✅ Ordine inviato:{result}")


    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"❌ Errore invio ordine: {result}")
        raise HTTPException(status_code=400, detail=f"Trade failed: {result.comment}")

    log(f"✅ Ordine eseguito correttamente: {result}")
    return {"message": "✅ Order sent", "result": result._asdict()}

# non usato per ora
@app.get("/server_status")
def server_status():
    """
    Stato completo del server MT5 locale.
    Usato dal manager per verificare se il server è pronto.
    """
    status = {
        "mt5_initialized": False,
        "terminal": None,
        "account": None,
        "ready": False
    }

    try:
        terminal = mt5.terminal_info()
        if terminal is None:
            return {
                **status,
                "error": "MT5 non inizializzato"
            }

        status["mt5_initialized"] = True
        status["terminal"] = {
            "name": terminal.name,
            "path": terminal.path,
            "company": terminal.company,
            "version": ".".join(map(str, mt5.version()))
        }

        account = mt5.account_info()
        if account:
            status["account"] = {
                "login": account.login,
                "server": account.server,
                "balance": account.balance,
                "equity": account.equity,
                "currency": account.currency
            }
            status["ready"] = True  # 🔥 server pronto a tradare

        return status

    except Exception as e:
        return {
            **status,
            "error": str(e)
        }

@app.post("/check-server")
def check_server(payload: dict):
    log(f"🔎 Check server ricevuto: {payload}")

    try:
        # Tenta di recuperare le info
        info = mt5.terminal_info()
        
        # Se info è None, proviamo a inizializzare al volo 
        # (magari l'agente è attivo ma il link al terminale è caduto)
        if info is None:
            if not mt5.initialize():
                return {
                    "status": "ko",
                    "message": f"MT5 non inizializzato. Errore: {mt5.last_error()}"
                }
            info = mt5.terminal_info()

        # Conversione versione in stringa leggibile
        v_tuple = mt5.version()
        version = f"{v_tuple[0]}.{v_tuple[1]} (Build {v_tuple[2]})"

        return {
            "status": "ok",
            "message": "MT5 raggiungibile",
            "mt5_version": version,
            "connected": info.connected, # Indica se il terminale è connesso ai server del broker
            "terminal": {
                "name": info.name,
                "company": info.company,
                "path": info.path
            }
        }

    except Exception as e:
        log(f"❌ Errore check-server: {e}")
        return {
            "status": "ko",
            "message": str(e)
        }

# Aggiungi questo import in alto se non c'è
from fastapi import Body

# ... (restante codice)

@app.post("/get_rates")
def get_rates(payload: dict = Body(...)):
    """
    Ritorna i dati storici (candele) per un simbolo e timeframe specifici.
    Payload: {"symbol": "EURUSD", "timeframe": 15, "n_candles": 100}
    """
    symbol = payload.get("symbol")
    timeframe = payload.get("timeframe")
    n_candles = payload.get("n_candles", 100)

    if not symbol or timeframe is None:
        raise HTTPException(status_code=400, detail="Missing symbol or timeframe")

    # 1. Recupero dati da MT5
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, n_candles)

    if rates is None or len(rates) == 0:
        return {"rates": []}

    # 2. Trasforma in DataFrame (gestisce automaticamente i tipi numpy)
    df = pd.DataFrame(rates)

    # 3. Converte il DataFrame in una lista di dizionari con tipi Python standard
    # .to_json() con orient="records" converte correttamente numpy.int64 in int e float64 in float
    rates_list = json.loads(df.to_json(orient="records"))

    return {"rates": rates_list}

# -----------------------
# BLOCCO DI AVVIO
# -----------------------
if __name__ == "__main__":
    if len(sys.argv) < 3:
        log("Usage: python mt5_api.py <MT5_PATH> <HOST:PORT>")
        sys.exit(1)
    # path di MT5
    mt5_path = sys.argv[1] 
    # host+porta
    host_port = sys.argv[2]
    host, port_str = host_port.split(":")
    port = int(port_str)

    if not os.path.exists(mt5_path):
        log(f"❌ Terminale MT5 non trovato: {mt5_path}")
        sys.exit(1)

    if not mt5.initialize(mt5_path):
        err = mt5.last_error()
        log(f"❌ Fallita inizializzazione MT5: {err}")
        sys.exit(1)

    log(f"✅ MT5 inizializzato correttamente da {mt5_path}")
    log(f"🟢 Avvio FastAPI su {host}:{port}")

    uvicorn.run(app, host=host, port=port)

