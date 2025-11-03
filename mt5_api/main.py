from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import MetaTrader5 as mt5
from config import settings

app = FastAPI(title=f"{settings.API_NAME} ({settings.MT5_PATH})")

class LoginRequest(BaseModel):
    login: int
    password: str
    server: str

@app.on_event("startup")
def startup_event():
    print(f"🟢 Starting MT5 API for: {settings.MT5_PATH}")
    if not mt5.initialize(settings.MT5_PATH):
        err = mt5.last_error()
        raise RuntimeError(f"❌ Failed to initialize MT5 ({settings.MT5_PATH}): {err}")

@app.on_event("shutdown")
def shutdown_event():
    print(f"🔴 Shutting down MT5 API: {settings.MT5_PATH}")
    mt5.shutdown()

@app.post("/login")
def login(req: LoginRequest):
    if not mt5.login(req.login, req.password, req.server):
        err = mt5.last_error()
        raise HTTPException(status_code=400, detail=f"Login failed: {err}")
    info = mt5.account_info()
    return {"message": "✅ Login OK", "balance": info.balance if info else None}

@app.post("/buy")
def buy(symbol: str, volume: float):
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY,
        "deviation": 10,
        "magic": 12345,
        "comment": "API BUY"
    }
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(status_code=400, detail=f"Trade failed: {result.comment}")
    return {"message": "✅ BUY sent", "order": result._asdict()}
