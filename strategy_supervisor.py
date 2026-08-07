# --- STRATEGY SUPERVISOR: agente AI che gestisce N strategie ---
#
# Raccoglie lo stato di mercato di ogni strategia attiva (ATR M5, regime,
# sessione, performance recente, posizioni aperte), lo invia a un LLM
# (OpenRouter) e produce RACCOMANDAZIONI (ACTIVE / PAUSE / WATCH) filtrate
# da regole di sicurezza deterministiche.
#
# IMPORTANTE: il supervisore NON applica nulla in autonomia. Le sue
# raccomandazioni vanno applicate manualmente dall'utente (endpoint /apply).
import os
import re
import json
import time
import threading
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import pandas as pd
import requests

from logger import log as global_log
from db import get_connection
from indicators.ta import compute_atr, compute_rolling_percentile
from trading_signals_multi2 import (
    get_data,
    get_current_session,
    is_market_open,
    is_night_time,
    compute_regime,
    REGIME_WINDOW,
    sessions,
    sessions_lock,
    STRATEGIES,
)

ROME = ZoneInfo("Europe/Rome")
ALLOWED_ACTIONS = {"ACTIVE", "PAUSE", "WATCH"}

DEFAULT_CONFIG = {
    "interval_seconds": 600,          # valutazione periodica (5-15 min)
    "model": "openrouter/free",       # modello OpenRouter per le raccomandazioni
    "enabled_trader_ids": [],         # [] = tutti i trader attivi con strategia
    "lockdown_on_news": True,         # regime NEWS -> PAUSE forzata
    "max_consecutive_losses": 6,      # kill switch: N perdite consecutive
    "min_trades_for_kill": 10,        # min trade chiusi prima del kill switch
    "min_win_rate_for_kill": 25.0,    # kill switch: win rate sotto soglia
    "drawdown_threshold": -300.0,     # kill switch: P&L netto ultimi 30 trade
    "fetch_positions": True,          # conta posizioni aperte dal slave
    "max_history": 20,                # storico cicli di valutazione
}


def now_str() -> str:
    return datetime.now(ROME).strftime("%Y-%m-%d %H:%M:%S")


def now_iso() -> str:
    return datetime.now(ROME).isoformat(timespec="seconds")


# ─────────────────────── HELPERS DB ───────────────────────

def _load_traders() -> list[dict]:
    """Tutti i trader attivi con strategia + info server slave."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT t.id, t.name, t.is_active, t.selected_signal, t.selected_symbol,
                   t.custom_signal_interval, t.sessions_filter,
                   ss.id AS slave_server_id, ss.ip AS slave_ip, ss.port AS slave_port
            FROM traders t
            LEFT JOIN servers ss ON ss.id = t.slave_server_id
            WHERE t.is_active = 1 AND t.selected_signal IS NOT NULL
              AND t.selected_signal != '' AND t.selected_symbol IS NOT NULL
              AND t.selected_symbol != ''
            ORDER BY t.id
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def _is_running(trader_id: int) -> bool:
    with sessions_lock:
        return trader_id in sessions and sessions[trader_id].get("timer") is not None


def _performance(trader_id: int) -> dict:
    """Metriche sugli ultimi 30 trade chiusi (da slave_orders)."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT profit FROM slave_orders
                WHERE trader_id = %s AND closed_at IS NOT NULL
                ORDER BY closed_at DESC LIMIT 30
            """, (trader_id,))
            rows = cursor.fetchall()
        finally:
            cursor.close()
            conn.close()
    except Exception:
        return {
            "total_trades": 0, "win_rate": None,
            "consecutive_losses": 0, "net_profit_30": 0.0,
        }

    profits = [float(r.get("profit") or 0) for r in rows]
    wins = [p for p in profits if p > 0]
    total = len(profits)
    wr = len(wins) / total * 100 if total else None

    consecutive_losses = 0
    for p in profits:
        if p <= 0:
            consecutive_losses += 1
        else:
            break

    return {
        "total_trades": total,
        "win_rate": round(wr, 1) if wr is not None else None,
        "consecutive_losses": consecutive_losses,
        "net_profit_30": round(sum(profits), 2),
    }


# ─────────────────────── SNAPSHOT MERCATO ───────────────────────

def _safe_float(series, idx=-2, default=None):
    try:
        v = series.iloc[idx]
        if pd.isna(v):
            v = series.iloc[-1]
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _market_snapshot(symbol: str, slave_url: str) -> dict:
    """Condizioni di mercato correnti per un simbolo (ATR M5, regime M15, sessione)."""
    try:
        df_m5 = get_data(symbol, 5, 100, slave_url)
        df_m15 = get_data(symbol, 15, 300, slave_url)

        if df_m5 is None or df_m5.empty:
            return {"ok": False, "error": "Nessun dato M5"}

        atr_m5 = _safe_float(compute_atr(df_m5))
        atr_m15_pct = None
        regime = "NORMAL"
        if df_m15 is not None and not df_m15.empty:
            atr_m15_pct = _safe_float(compute_rolling_percentile(compute_atr(df_m15), REGIME_WINDOW))
            regime = compute_regime(atr_m15_pct, is_spike=False)

        return {
            "ok": True,
            "atr_m5": round(atr_m5, 5) if atr_m5 is not None else None,
            "atr_m15_pct": round(atr_m15_pct, 3) if atr_m15_pct is not None else None,
            "regime": regime,
            "session": get_current_session(),
            "market_open": is_market_open(symbol),
            "night": is_night_time(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _open_positions(slave_url: str, symbol: str) -> int:
    try:
        resp = requests.get(f"{slave_url}/positions", timeout=5)
        if resp.status_code == 200:
            return len([p for p in resp.json() if p.get("symbol") == symbol])
    except Exception:
        pass
    return 0


# ─────────────────────── RACCOLTA STATO ───────────────────────

def _collect_states(cfg: dict) -> list[dict]:
    traders = _load_traders()
    allowed = set(cfg.get("enabled_trader_ids") or [])
    states = []

    for t in traders:
        tid = t["id"]
        if allowed and tid not in allowed:
            continue

        slave_ip = t.get("slave_ip")
        slave_port = t.get("slave_port")
        if not slave_ip or not slave_port:
            states.append({
                "trader_id": tid,
                "name": t.get("name"),
                "strategy": t.get("selected_signal"),
                "symbol": t.get("selected_symbol"),
                "slave_url": None,
                "current_status": "STOPPED",
                "open_positions": 0,
                "market": {"ok": False, "error": "Server slave non configurato"},
                "performance": {
                    "total_trades": 0, "win_rate": None,
                    "consecutive_losses": 0, "net_profit_30": 0.0,
                },
            })
            continue

        slave_url = f"http://{slave_ip}:{slave_port}"
        symbol = t.get("selected_symbol")

        market = _market_snapshot(symbol, slave_url)
        perf = _performance(tid)
        pos = _open_positions(slave_url, symbol) if cfg.get("fetch_positions") else 0

        states.append({
            "trader_id": tid,
            "name": t.get("name"),
            "strategy": t.get("selected_signal"),
            "symbol": symbol,
            "slave_url": slave_url,
            "current_status": "RUNNING" if _is_running(tid) else "STOPPED",
            "open_positions": pos,
            "market": market,
            "performance": perf,
        })

    return states


# ─────────────────────── REGOLE DI SICUREZZA ───────────────────────

def _safety_check(state: dict, cfg: dict) -> tuple[Optional[str], Optional[str]]:
    """Restituisce (azione forzata, motivo) oppure (None, None)."""
    market = state.get("market") or {}
    perf = state.get("performance") or {}
    forced = None
    reason = None

    if not market.get("market_open", True):
        forced, reason = "PAUSE", "Mercato chiuso"
    elif market.get("night"):
        forced, reason = "PAUSE", "Orario notturno a bassa liquidità"
    elif cfg.get("lockdown_on_news") and market.get("regime") == "NEWS":
        forced, reason = "PAUSE", "Regime NEWS (spike di volatilità)"
    elif perf.get("total_trades", 0) >= cfg.get("min_trades_for_kill", 10):
        if perf.get("consecutive_losses", 0) >= cfg.get("max_consecutive_losses", 6):
            forced = "PAUSE"
            reason = f"{perf['consecutive_losses']} perdite consecutive"
        elif perf.get("win_rate") is not None and perf["win_rate"] < cfg.get("min_win_rate_for_kill", 25.0):
            forced = "PAUSE"
            reason = f"Win rate {perf['win_rate']}% sotto soglia"
        elif perf.get("net_profit_30", 0.0) < cfg.get("drawdown_threshold", -300.0):
            forced = "PAUSE"
            reason = f"P&L ultimi 30 trade {perf['net_profit_30']}$ sotto soglia"

    return forced, reason


# ─────────────────────── LLM ───────────────────────

def _build_prompt(states: list[dict]) -> str:
    lines = []
    for s in states:
        m = s.get("market") or {}
        p = s.get("performance") or {}
        lines.append(
            f"- trader_id={s['trader_id']} | nome={s['name']} | strategia={s['strategy']} | "
            f"simbolo={s['symbol']} | stato={s['current_status']} | posizioni_aperte={s.get('open_positions', 0)}\n"
            f"  mercato: sessione={m.get('session', '?')} aperto={m.get('market_open')} "
            f"regime={m.get('regime', '?')} atr_m5={m.get('atr_m5', '?')} atr_m15_pct={m.get('atr_m15_pct', '?')}\n"
            f"  performance: trade_ultimi30={p.get('total_trades')} win_rate={p.get('win_rate')} "
            f"perdite_consecutive={p.get('consecutive_losses')} pnl_30={p.get('net_profit_30')}"
        )

    strategies_info = "\n".join(
        f"  - {name}: {strat.__class__.__name__}"
        for name, strat in STRATEGIES.items()
    )

    states_block = "\n".join(lines) if lines else "Nessuna strategia attiva."

    return f"""Sei il supervisore di un sistema di trading automatico su MetaTrader 5.
Gestisci N strategie (ognuna legata a un "trader" = una coppia strategia/simbolo) e decidi per ciascuna se:
- ACTIVE  : tenerla attiva, le condizioni di mercato sono favorevoli.
- PAUSE   : sospenderla, condizioni avverse o performance in caduta.
- WATCH   : tenerla attiva ma monitorarla, condizioni incerte.

Momento attuale: {now_str()} (ora Roma).

## Strategie disponibili nel sistema
{strategies_info}

## Stato attuale di ogni strategia (una per trader)
{states_block}

## Valutazione
Considera per ogni strategia:
- Sessione corrente e regime di volatilità (NEWS/TREND/NORMAL/RANGE) e ATR M5.
- Performance recente (win rate, perdite consecutive, P&L ultimi 30 trade).
- Presenza di posizioni aperte.
Metti in PAUSE le strategie in regime NEWS o RANGE con segnali deboli, con perdite consecutive
o win rate crollato. Tieni ACTIVE solo condizioni pulite. Usa WATCH per i casi incerti.

## OUTPUT (SOLO JSON valido, niente testo fuori dal JSON)
Rispondi con un JSON nel formato esatto:
{{"reasoning": "sintesi in italiano (max 150 parole)", "decisions": {{"<trader_id>": {{"action": "ACTIVE", "confidence": 0.8, "reason": "motivo breve in italiano"}}}}}}
Le chiavi di decisions devono essere i trader_id presenti sopra. Action ammesse: ACTIVE, PAUSE, WATCH."""


def _parse_llm_json(content: str) -> Optional[dict]:
    if not content:
        return None
    content = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if fence:
        content = fence.group(1)
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(content[start:end + 1])
    except Exception:
        return None


def _call_llm(prompt: str, model: str) -> Optional[dict]:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        global_log("🧠 Supervisor: OPENROUTER_API_KEY mancante — decisioni solo con regole di safety")
        return None

    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=90)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
        )
        content = response.choices[0].message.content or ""
        return _parse_llm_json(content)
    except Exception as e:
        global_log(f"🧠 Supervisor: errore LLM: {e}")
        return None


# ─────────────────────── MERGE / VALIDAZIONE ───────────────────────

def _merge_recommendations(states: list[dict], llm: Optional[dict], cfg: dict) -> list[dict]:
    llm_decisions = (llm or {}).get("decisions") or {}
    recommendations = []

    for s in states:
        tid = str(s["trader_id"])
        dec = llm_decisions.get(tid) if isinstance(llm_decisions, dict) else None
        dec = dec if isinstance(dec, dict) else {}

        raw_action = str(dec.get("action", "")).strip().upper()
        action = raw_action if raw_action in ALLOWED_ACTIONS else "WATCH"

        try:
            confidence = max(0.0, min(1.0, float(dec.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5

        reason = str(dec.get("reason", "")).strip()[:500]

        forced, safety_reason = _safety_check(s, cfg)
        safety_override = False
        if forced:
            safety_override = True
            if action == forced:
                reason = safety_reason + (" — " + reason if reason else "")
            else:
                reason = safety_reason
            action = forced

        recommendations.append({
            "trader_id": s["trader_id"],
            "name": s.get("name"),
            "strategy": s.get("strategy"),
            "symbol": s.get("symbol"),
            "current_status": s.get("current_status"),
            "open_positions": s.get("open_positions", 0),
            "recommended_action": action,
            "confidence": confidence,
            "reason": reason,
            "safety_override": safety_override,
            "safety_reason": safety_reason,
            "market": s.get("market"),
            "performance": s.get("performance"),
        })

    return recommendations


# ─────────────────────── SUPERVISOR ───────────────────────

class StrategySupervisor:
    def __init__(self, config: Optional[dict] = None):
        self._lock = threading.Lock()
        self.config: dict = {**DEFAULT_CONFIG, **(config or {})}
        self._running = False
        self._timer: Optional[threading.Timer] = None
        self._in_progress = False

        self.last_run: Optional[str] = None
        self.last_error: Optional[str] = None
        self.recommendations: list[dict] = []
        self.llm_used = False
        self.reasoning = ""
        self.history: list[dict] = []
        self.started_at: Optional[str] = None

    # ── gestione ciclo ──

    def start(self, config: Optional[dict] = None) -> dict:
        if config:
            self.config = {**self.config, **config}
        with self._lock:
            if self._running:
                return {"status": "already_running", **self.status_locked()}
            self._running = True
            self.started_at = now_iso()
        global_log(f"🧠 Supervisor AVVIATO (intervallo {self.config.get('interval_seconds')}s)")
        self._schedule()
        return {"status": "started", "config": dict(self.config)}

    def stop(self) -> dict:
        with self._lock:
            self._running = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
        global_log("🧠 Supervisor FERMATO")
        return {"status": "stopped"}

    def _schedule(self):
        with self._lock:
            if not self._running:
                return
            interval = max(60, int(self.config.get("interval_seconds", 600)))
            t = threading.Timer(interval, self._tick)
            t.daemon = True
            self._timer = t
        t.start()

    def _tick(self):
        self.run_once()
        self._schedule()

    # ── valutazione ──

    def run_once(self) -> dict:
        with self._lock:
            if self._in_progress:
                return self._status_payload()
            self._in_progress = True

        try:
            states = _collect_states(self.config)
            llm = _call_llm(_build_prompt(states), self.config.get("model", "openrouter/free")) if states else None
            recs = _merge_recommendations(states, llm, self.config)
            with self._lock:
                self.recommendations = recs
                self.llm_used = llm is not None
                self.reasoning = str((llm or {}).get("reasoning", ""))[:2000]
                self.last_run = now_iso()
                self.last_error = None
                self.history.append({
                    "ts": now_iso(),
                    "count": len(recs),
                    "llm": llm is not None,
                })
                self.history = self.history[-int(self.config.get("max_history", 20)):]
            global_log(f"🧠 Supervisor: valutazione completata — {len(recs)} strategie, "
                       f"LLM={'usato' if llm else 'non usato (solo safety)'}")
        except Exception as e:
            traceback.print_exc()
            with self._lock:
                self.last_run = now_iso()
                self.last_error = f"{e}"
            global_log(f"🧠 Supervisor: errore valutazione: {e}")
        finally:
            with self._lock:
                self._in_progress = False

        return self._status_payload()

    def _managed_traders(self) -> list[dict]:
        """Trader attivi gestiti in questo momento (aggiornato dal DB ad ogni status)."""
        allowed = set(self.config.get("enabled_trader_ids") or [])
        managed = []
        try:
            traders = _load_traders()
        except Exception:
            return managed
        for t in traders:
            tid = t["id"]
            if allowed and tid not in allowed:
                continue
            managed.append({
                "trader_id": tid,
                "name": t.get("name"),
                "strategy": t.get("selected_signal"),
                "symbol": t.get("selected_symbol"),
                "interval": t.get("custom_signal_interval"),
                "current_status": "RUNNING" if _is_running(tid) else "STOPPED",
            })
        return managed

    # ── status ──

    def _status_payload(self) -> dict:
        return {
            "running": self._running,
            "started_at": self.started_at,
            "last_run": self.last_run,
            "last_error": self.last_error,
            "llm_used": self.llm_used,
            "reasoning": self.reasoning,
            "interval_seconds": self.config.get("interval_seconds"),
            "config": dict(self.config),
            "managed_traders": self._managed_traders(),
            "recommendations": self.recommendations,
            "history": self.history,
        }

    def status_locked(self) -> dict:
        return self._status_payload()

    def get_status(self) -> dict:
        with self._lock:
            return self._status_payload()


# ─────────────────────── SINGLETON + HELPER ───────────────────────

supervisor = StrategySupervisor()


def start_supervisor(config: Optional[dict] = None) -> dict:
    return supervisor.start(config)


def stop_supervisor() -> dict:
    return supervisor.stop()


def run_now() -> dict:
    return supervisor.run_once()


def get_status() -> dict:
    return supervisor.get_status()


def update_config(cfg: dict) -> dict:
    merged = {**supervisor.config, **cfg}
    with supervisor._lock:
        supervisor.config = merged
        interval = max(60, int(merged.get("interval_seconds", 600)))
        if supervisor._running:
            if supervisor._timer:
                supervisor._timer.cancel()
            t = threading.Timer(interval, supervisor._tick)
            t.daemon = True
            supervisor._timer = t
            t.start()
    global_log(f"🧠 Supervisor: config aggiornata {json.dumps(merged, default=str)}")
    return {"status": "ok", "config": dict(merged)}
