from fastapi import FastAPI
from routers import trading_router
import MetaTrader5 as mt5
import logging

def create_mt5_app(name: str, path: str, server: str, login: int, password: str) -> FastAPI:
    app = FastAPI(title=f"{name} - MT5 API")

    @app.on_event("startup")
    def startup_event():
        logging.info(f"Initializing {name} at {path}")
        if not mt5.initialize(path=path, server=server, login=login, password=password):
            logging.error(f"Failed to initialize {name}: {mt5.last_error()}")
        else:
            logging.info(f"{name} initialized successfully")

    @app.on_event("shutdown")
    def shutdown_event():
        mt5.shutdown()
        logging.info(f"{name} shutdown complete")

    app.include_router(trading_router.router, prefix=f"/{name.lower()}", tags=[name])
    return app
