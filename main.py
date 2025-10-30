import logging
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv
import MetaTrader5 as mt5

# i files a cui punta il main: db.py, mt5_routes.py
from db import router as db_router
from mt5_routes import router as mt5_router

# --- LOGGING ---
log_file_path = "./fxscript.log"
logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logging.info("Starting API")

# --- APP ---
app = FastAPI(title="MT5 Manager API")

# --- CORS ---
origins = ["http://localhost:4200", "http://127.0.0.1:4200"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ROUTERS /db e /mt5 sono i prefissi da aggiungere alle chiamate FE---
app.include_router(db_router, prefix="/db", tags=["Database"])
app.include_router(mt5_router, prefix="/mt5", tags=["MetaTrader5"])

# --- STARTUP ---
@app.on_event("startup")
def startup_event():
    path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if not os.path.exists(path):
        logging.error(f"MetaTrader5 path not found: {path}")
        raise FileNotFoundError(f"{path} does not exist")

    login = "959911"
    password = "Qpnldan1@1"
    server = "VTMarkets-Demo"

    if not mt5.initialize(path, login=int(login), password=password, server=server):
        logging.error(f"MT5 initialize failed: {mt5.last_error()}")
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    logging.info("MT5 initialized successfully")

# --- ERROR HANDLERS ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    logging.error(f"Validation error: {exc.errors()} for body {body.decode()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors(), "body": body.decode()})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc)})

# --- BASIC ROUTES ---
@app.get("/")
def root():
    return {"status": "running", "message": "MT5 Manager API active"}

@app.get("/healthz")
def healthz():
    info = mt5.account_info()
    if info:
        return {"status": "ok", "account": info.login}
    return {"status": "error", "details": mt5.last_error()}

# --- RUN SERVER ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
