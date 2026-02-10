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


@router.post("/check-server")
async def manager_check_server(payload: dict):
    # L'host e port arrivano da Angular
    agent_url = f"http://{payload['host']}:{payload['port']}/check-server"
    # Chiamata all'agente mt5_api (che abbiamo memorizzato prima)
    r = requests.post(agent_url, json={}, timeout=5) 
    return r.json()


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


