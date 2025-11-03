# mt5_api.py
import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sys

app = FastAPI(title="MT5 Local API")

class LoginRequest(BaseModel):
    login: int
    password: str
    server: str

@app.on_event("startup")
def startup_event():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"🟢 Starting MT5 terminal at path: {path}")
    if not mt5.initialize(path):
        err = mt5.last_error()
        raise RuntimeError(f"❌ Failed to initialize: {err}")

@app.on_event("shutdown")
def shutdown_event():
    mt5.shutdown()

@app.post("/login")
def login(req: LoginRequest):
    if not mt5.login(req.login, req.password, req.server):
        err = mt5.last_error()
        raise HTTPException(status_code=400, detail=f"Login failed: {err}")
    info = mt5.account_info()
    return {"message": "✅ Login OK", "balance": info.balance if info else None}

@app.get("/positions")
def get_positions():
    positions = mt5.positions_get()
    if positions is None:
        raise HTTPException(status_code=400, detail="Cannot get positions")
    return [p._asdict() for p in positions]

@app.post("/order")
def send_order(order: dict):
    result = mt5.order_send(order)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise HTTPException(status_code=400, detail=f"Trade failed: {result.comment}")
    return {"message": "✅ Order sent", "result": result._asdict()}

if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
