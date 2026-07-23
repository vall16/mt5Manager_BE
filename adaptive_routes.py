# --- ADAPTIVE ROUTES: API per gestire gli agenti adaptive ---
from pydantic import BaseModel
from typing import Optional
from fastapi import APIRouter
from logger import log as global_log
from adaptive_agent import AdaptiveAgent

router = APIRouter()

# --- stato agenti in memoria (trader_id -> AdaptiveAgent) ---
agents: dict[int, AdaptiveAgent] = {}


class AdaptiveStartRequest(BaseModel):
    trader_id: int
    strategy_name: str
    symbol: str


class AdaptiveStopRequest(BaseModel):
    trader_id: int


# ------------------------------------------------------------------ #
#  START
# ------------------------------------------------------------------ #

@router.post("/start")
def start_adaptive(req: AdaptiveStartRequest):
    agent = AdaptiveAgent(req.strategy_name, req.symbol)
    agents[req.trader_id] = agent
    global_log(f"🧬 Adaptive agent AVVIATO per trader {req.trader_id} ({req.strategy_name} / {req.symbol})")
    return {
        "status": "started",
        "trader_id": req.trader_id,
        "params": agent.get_params(),
    }


# ------------------------------------------------------------------ #
#  STOP
# ------------------------------------------------------------------ #

@router.post("/stop")
def stop_adaptive(req: AdaptiveStopRequest):
    if req.trader_id in agents:
        del agents[req.trader_id]
        global_log(f"🧬 Adaptive agent FERMATO per trader {req.trader_id}")
        return {"status": "stopped", "trader_id": req.trader_id}
    return {"status": "not_running", "trader_id": req.trader_id}


# ------------------------------------------------------------------ #
#  STATUS
# ------------------------------------------------------------------ #

@router.get("/status/{trader_id}")
def get_status(trader_id: int):
    agent = agents.get(trader_id)
    if not agent:
        return {"active": False, "trader_id": trader_id}
    return {"active": True, "trader_id": trader_id, **agent.get_status()}


# ------------------------------------------------------------------ #
#  STATS (analisi on-demand)
# ------------------------------------------------------------------ #

@router.get("/stats/{trader_id}")
def get_stats(trader_id: int):
    agent = agents.get(trader_id)
    if not agent:
        return {"active": False, "trader_id": trader_id}
    stats = agent.analyze()
    return {"active": True, "trader_id": trader_id, "stats": stats}


# ------------------------------------------------------------------ #
#  HELPER: accesso agenti da altri moduli
# ------------------------------------------------------------------ #

def get_agent(trader_id: int) -> Optional[AdaptiveAgent]:
    """Restituisce l'agent per un trader, o None se non attivo."""
    return agents.get(trader_id)
