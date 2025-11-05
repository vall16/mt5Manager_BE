# mt5_api.py: sta su ogni SERVER su cui viene istanziato mt5
import os
import MetaTrader5 as mt5
import concurrent
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sys

app = FastAPI(title="MT5 Local API")

class LoginRequest(BaseModel):
    login: int
    password: str
    server: str
    
class InitRequest(BaseModel):
    path: str


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

    # 🔹 Recupera il path da linea di comando, se non passato None
    path = sys.argv[1] if len(sys.argv) > 1 else None

    if not path:
        raise RuntimeError("❌ Nessun percorso MT5 fornito. Usa: python main.py <MT5_PATH> [PORT]")

    print(f"🟢 Avvio terminale MT5 al path: {path}")

    # 🔹 Inizializza MT5
    initialized = mt5.initialize(path)
    if not initialized:
        err = mt5.last_error()  # Recupera dettagli dell'errore
        raise RuntimeError(f"❌ Fallita inizializzazione MT5 ({path}): {err}")

    print(f"✅ MT5 inizializzato correttamente al path: {path}")


@app.on_event("shutdown")
def shutdown_event():
    mt5.shutdown()

# @app.post("/login")
# def login(req: LoginRequest):
#     if not mt5.login(req.login, req.password, req.server):
#         err = mt5.last_error()
#         raise HTTPException(status_code=400, detail=f"Login failed: {err}")
#     info = mt5.account_info()
#     return {"message": "✅ Login OK", "balance": info.balance if info else None}

# import concurrent.futures
# import MetaTrader5 as mt5
# from fastapi import HTTPException

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



@app.post("/order")
def send_order(order: dict):
    result = mt5.order_send(order)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(status_code=400, detail=f"Trade failed: {result.comment}")
    return {"message": "✅ Order sent", "result": result._asdict()}

if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=False)
