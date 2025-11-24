import logging
import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv
import requests

# i files a cui punta il main: db.py, mt5_routes.py
from db import router as db_router
from mt5_routes import router as mt5_router
from trading_signals import router as trade_router
# from trading_signals import start_polling

# MT5_API_URL = "http://127.0.0.1:8081"
# MT5_API_KEY = "superkey123"

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

# --- ROUTERS /db e /mt5 e /app sono i prefissi da aggiungere alle chiamate FE---
app.include_router(db_router, prefix="/db", tags=["Database"])
app.include_router(mt5_router, prefix="/mt5", tags=["MetaTrader5"])
app.include_router(trade_router, prefix="/trade", tags=["AppTrader5"])


# --- STARTUP ---
@app.on_event("startup")
def startup_event():
    """
    Evento di avvio dell'API Manager:
    - verifica connessione al DB
    - crea file di log
    - NON inizializza MetaTrader
    """
    logging.info("✅ Manager API avviata correttamente. Nessuna inizializzazione MT5 al startup.")
    # start_polling()



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

# --- RUN SERVER ---
if __name__ == "__main__":
    import uvicorn
    # uvicorn.run("main:app", host="127.0.0.1", port=8080)
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True)
