"""
Signal Research API — ottimizzazione SL/TP per multiple strategie.
"""
import uuid
import io
import threading
import logging
from itertools import product
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
from fastapi.responses import JSONResponse, StreamingResponse

from logger import log as global_log

router = APIRouter()

AUTO_LOG_FILE = "auto_discover_log.txt"

# Session storage
research_sessions = {}
research_lock = threading.Lock()


class SignalResearchRequest(BaseModel):
    symbol: str
    timeframe: str = "M15"
    days: int = 90
    lot: float = 0.01
    balance: float = 1000
    sl_min: int = 100
    sl_max: int = 600
    sl_step: int = 50
    tp_min: int = 200
    tp_max: int = 1200
    tp_step: int = 100
    strategies: List[str]
    direction: str = "both"
    trader_id: Optional[int] = None


def _get_mt5_api_url(trader_id: int) -> Optional[str]:
    from db import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT ss.ip, ss.port
            FROM traders t
            JOIN servers ss ON ss.id = t.slave_server_id
            WHERE t.id = %s
        """, (trader_id,))
        row = cursor.fetchone()
        if row and row[0] and row[1]:
            return f"http://{row[0]}:{row[1]}"
    finally:
        cursor.close()
        conn.close()
    return None


def _run_optimization(session_id: str, config: dict):
    from backtest import run_backtest_api, fetch_data, precompute_indicators, STRATEGIES

    mt5_api_url = config["mt5_api_url"]
    symbol = config["symbol"]
    days = config["days"]
    direction = config.get("direction", "both")
    sl_range = list(range(config["sl_min"], config["sl_max"] + 1, config["sl_step"]))
    tp_range = list(range(config["tp_min"], config["tp_max"] + 1, config["tp_step"]))

    # Direction: "buy", "sell", or "both" (separate buy+sell)
    directions = ["buy", "sell"] if direction == "both" else [direction]

    all_combos = [(s, sl, tp, d) for s in config["strategies"] for sl, tp in product(sl_range, tp_range) if tp > sl for d in directions]
    total = len(all_combos)

    cancel = lambda: research_sessions.get(session_id, {}).get("cancelled", False)

    # Group strategies by timeframe requirements → fetch data once per group
    dfs_cache = {}
    strategy_dfs = {}
    strategy_errors = {}
    for sname in config["strategies"]:
        strat = STRATEGIES.get(sname)
        if not strat:
            continue
        tf_key = (strat.requires_m1, strat.requires_m5, strat.requires_m15, getattr(strat, 'requires_h1', False))
        if tf_key not in dfs_cache:
            print(f"[SignalResearch] Fetching data for TF set M1={tf_key[0]} M5={tf_key[1]} M15={tf_key[2]} H1={tf_key[3]}...")
            try:
                dfs = fetch_data(symbol, strat, days, mt5_api_url)
                precompute_indicators(dfs)
                dfs_cache[tf_key] = dfs
            except Exception as e:
                print(f"[SignalResearch] Error fetching data for {sname}: {e}")
                dfs_cache[tf_key] = None
                strategy_errors[sname] = str(e)
        strategy_dfs[sname] = dfs_cache[tf_key]
        if dfs_cache[tf_key] is None:
            strategy_errors.setdefault(sname, "Nessun dato disponibile per questa strategia sul simbolo selezionato")

    # Update total after filtering invalid strategies
    valid_combos = [(s, sl, tp, d) for s, sl, tp, d in all_combos if strategy_dfs.get(s) is not None]
    total = len(valid_combos)

    results = []
    for idx, (strategy, sl, tp, dirn) in enumerate(valid_combos):
        if cancel():
            with research_lock:
                if session_id in research_sessions:
                    research_sessions[session_id]["status"] = "cancelled"
            return

        try:
            result = run_backtest_api(
                strategy_name=strategy,
                symbol=symbol,
                days=days,
                lot=config["lot"],
                balance=config["balance"],
                mt5_api_url=mt5_api_url,
                cancel_flag=cancel,
                direction=dirn,
                pre_fetched_dfs=strategy_dfs[strategy],
                skip_indicators=True,
                sl_pts=sl,
                tp_pts=tp,
                verbose=False,
            )

            summary = result.get("summary", {})
            trades = result.get("trades", [])

            # Compute max DD percentage
            net_pnl = summary.get("net_pnl", 0)
            init_bal = config["balance"]
            return_pct = (net_pnl / init_bal * 100) if init_bal else 0

            # Max drawdown from trade balance curve
            peak = init_bal
            max_dd_abs = 0
            for t in trades:
                bal = t.get("balance", init_bal)
                peak = max(peak, bal)
                dd = bal - peak
                if dd < max_dd_abs:
                    max_dd_abs = dd
            max_dd_pct = (max_dd_abs / init_bal * 100) if init_bal else 0

            # Sharpe-like ratio
            win_rate = summary.get("win_rate", 0)
            avg_win = summary.get("avg_win", 0)
            avg_loss = abs(summary.get("avg_loss", 1))
            reward_risk = avg_win / avg_loss if avg_loss > 0 else 1
            sharpe = (win_rate / 100 * reward_risk - (1 - win_rate / 100)) if win_rate else 0

            total_trades = summary.get("total_trades", 0)
            trades_per_day = total_trades / config["days"] if config["days"] > 0 else 0

            # Average hold (from trades list)
            avg_hold = 0
            if trades:
                # Try to compute avg hold from trade times
                avg_hold = 0  # will be computed by the strategy

            results.append({
                "strategy": strategy,
                "sl": sl,
                "tp": tp,
                "direction": dirn,
                "max_hold": 30,
                "trades": total_trades,
                "win_rate": round(win_rate, 1),
                "return_pct": round(return_pct, 1),
                "max_dd": round(max_dd_pct, 1),
                "avg_hold": round(avg_hold, 0),
                "sharpe": round(sharpe, 2),
            })

        except Exception as e:
            logging.error(f"Signal research error for {strategy} SL={sl} TP={tp}: {e}")
            continue

        # Update progress
        pct = int((idx + 1) / total * 100)
        with research_lock:
            if session_id in research_sessions:
                research_sessions[session_id]["progress"] = pct

    # Sort by return_pct descending
    results.sort(key=lambda x: x["return_pct"], reverse=True)

    with research_lock:
        if session_id in research_sessions:
            if research_sessions[session_id]["cancelled"]:
                research_sessions[session_id]["status"] = "cancelled"
            elif not results and strategy_errors:
                err_msgs = "; ".join(f"{s}: {e}" for s, e in strategy_errors.items())
                research_sessions[session_id]["status"] = "error"
                research_sessions[session_id]["result"] = {
                    "error": f"Nessun dato disponibile per le strategie selezionate su {symbol}. {err_msgs}"
                }
            else:
                research_sessions[session_id]["status"] = "done"
                research_sessions[session_id]["result"] = {"results": results}


@router.post("/signal-research/run")
def start_signal_research(req: SignalResearchRequest):
    if not req.strategies:
        return JSONResponse(status_code=400, content={"error": "No strategies selected"})

    # Resolve MT5 API URL
    mt5_api_url = None
    if req.trader_id:
        mt5_api_url = _get_mt5_api_url(req.trader_id)

    if not mt5_api_url:
        # Try default trader
        from db import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT ss.ip, ss.port
                FROM traders t
                JOIN servers ss ON ss.id = t.slave_server_id
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row and row[0] and row[1]:
                mt5_api_url = f"http://{row[0]}:{row[1]}"
        finally:
            cursor.close()
            conn.close()

    if not mt5_api_url:
        return JSONResponse(status_code=400, content={"error": "No MT5 API URL found"})

    session_id = str(uuid.uuid4())[:8]

    config = {
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "days": req.days,
        "lot": req.lot,
        "balance": req.balance,
        "sl_min": req.sl_min,
        "sl_max": req.sl_max,
        "sl_step": req.sl_step,
        "tp_min": req.tp_min,
        "tp_max": req.tp_max,
        "tp_step": req.tp_step,
        "strategies": req.strategies,
        "direction": req.direction,
        "mt5_api_url": mt5_api_url,
    }

    with research_lock:
        research_sessions[session_id] = {
            "status": "running",
            "result": None,
            "cancelled": False,
            "progress": 0,
            "config": config,
        }

    t = threading.Thread(target=_run_optimization, args=(session_id, config), daemon=True)
    t.start()

    return {"session_id": session_id}


@router.get("/signal-research/{session_id}")
def get_research_status(session_id: str):
    with research_lock:
        session = research_sessions.get(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return {
        "status": session["status"],
        "result": session["result"],
        "progress": session.get("progress", 0),
    }


@router.post("/signal-research/{session_id}/cancel")
def cancel_research(session_id: str):
    with research_lock:
        session = research_sessions.get(session_id)
        if not session:
            return JSONResponse(status_code=404, content={"error": "Session not found"})
        session["cancelled"] = True
    return {"status": "cancelling"}


# ── Auto Discover ──────────────────────────────────────────────

class AutoDiscoverRequest(BaseModel):
    symbol: str
    days: int = 90
    lot: float = 0.01
    balance: float = 1000
    direction: str = "both"
    target_return: float = 30.0
    min_trades: int = 20
    volume_filter: bool = False
    sessions_filter: str = ""
    use_spread: bool = True


# Spread stimato in pips (usato solo nel backtest auto, per non sovrastimare i rendimenti)
SPREAD_PIPS = {
    "XAUUSD": 25.0,
    "EURUSD": 1.0,
    "GBPUSD": 1.2,
    "USDJPY": 0.5,
    "GBPJPY": 1.2,
    "AUDJPY": 0.8,
    "MSFT": 3.0,
    "MSFT.NAS": 3.0,
    "NVDA": 3.0,
    "NVDA.NAS": 3.0,
}


def _summarize_trades(trades, initial):
    net_pnl = sum(t[3] for t in trades)
    return_pct = net_pnl / initial * 100 if initial else 0
    wins = [t for t in trades if t[3] > 0]
    losses = [t for t in trades if t[3] < 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t[3] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t[3] for t in losses) / len(losses)) if losses else 0
    reward_risk = avg_win / avg_loss if avg_loss > 0 else 1
    sharpe = (win_rate / 100 * reward_risk - (1 - win_rate / 100)) if win_rate else 0

    peak = initial
    max_dd_abs = 0
    for t in trades:
        bal = t[4]
        peak = max(peak, bal)
        dd = bal - peak
        if dd < max_dd_abs:
            max_dd_abs = dd
    max_dd_pct = max_dd_abs / initial * 100 if initial else 0

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "return_pct": return_pct,
        "max_dd": max_dd_pct,
        "sharpe": sharpe,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


def _get_sl_tp_ranges(symbol: str):
    sym = symbol.upper()
    if "XAU" in sym or "GOLD" in sym:
        return 200, 800, 50, 400, 1600, 100
    if sym in ("NVDA", "NVDA.NAS", "AMD", "TSLA", "AAPL"):
        return 300, 1500, 100, 600, 3000, 200
    if sym in ("MSFT", "MSFT.NAS", "AMZN", "GOOGL"):
        return 200, 600, 50, 400, 1500, 100
    if sym in ("EURUSD", "GBPUSD", "GBPJPY", "AUDJPY", "USDJPY"):
        return 30, 200, 10, 60, 400, 20
    return 100, 600, 50, 200, 1200, 100


def _prepare_split_cache(dfx, ema_periods, rsi_periods, volume_filter, sessions):
    """Precalcola indicatori/filtri una sola volta per un dataframe."""
    from indicators.ta import compute_ema, compute_rsi
    from backtest import _get_session_label
    import numpy as np
    import pandas as pd

    ema_cache = {p: compute_ema(dfx, p).to_numpy(dtype=float) for p in ema_periods}
    rsi_cache = {p: compute_rsi(dfx, p).to_numpy(dtype=float) for p in rsi_periods}
    vol_arr = None
    if volume_filter:
        vol = dfx["tick_volume"].to_numpy(dtype=float)
        vol_avg = pd.Series(vol).rolling(20).mean().to_numpy()
        vol_arr = np.nan_to_num(vol > vol_avg)
    sess_arr = None
    if sessions:
        sess_arr = [_get_session_label(t) for t in dfx["time"].to_numpy()]
    return ema_cache, rsi_cache, vol_arr, sess_arr


def _run_generic_backtest(df, ema_fast, ema_slow, rsi_period, rsi_oversold, rsi_overbought, sl_pts, tp_pts, direction, lot, balance, pip, contract, spread_pips=0.0, volume_filter=False, sessions=None, ema_cache=None, rsi_arr=None, sess_arr=None, vol_ok_arr=None, rsi_cache=None):
    import numpy as np
    import pandas as pd
    from indicators.ta import compute_ema, compute_rsi
    from backtest import _get_session_label

    close = df["close"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    times = df["time"].to_numpy()

    if ema_cache is None:
        ema_cache = {}
    if ema_fast not in ema_cache:
        ema_cache[ema_fast] = compute_ema(df, ema_fast).to_numpy(dtype=float)
    if ema_slow not in ema_cache:
        ema_cache[ema_slow] = compute_ema(df, ema_slow).to_numpy(dtype=float)
    ema_f = ema_cache[ema_fast]
    ema_s = ema_cache[ema_slow]

    if rsi_arr is None:
        if rsi_cache is not None and rsi_period in rsi_cache:
            rsi_arr = rsi_cache[rsi_period]
        else:
            rsi_arr = compute_rsi(df, rsi_period).to_numpy(dtype=float)
            if rsi_cache is not None:
                rsi_cache[rsi_period] = rsi_arr
    rsi = rsi_arr

    if vol_ok_arr is None and volume_filter:
        vol = df["tick_volume"].to_numpy(dtype=float)
        vol_avg = pd.Series(vol).rolling(20).mean().to_numpy()
        vol_ok_arr = np.nan_to_num(vol > vol_avg)

    if sess_arr is None and sessions:
        sess_arr = [_get_session_label(t) for t in times]

    cost = spread_pips * pip * contract * lot
    trades = []
    position = None
    entry = dirn = sl_price = tp_price = 0.0
    lookback = max(ema_slow, rsi_period) + 5
    total = len(close)
    lot_contract = lot * contract

    for i in range(lookback, total):
        price = close[i]
        lo = low[i]
        hi = high[i]

        if position:
            if dirn == 0:  # buy
                if lo <= sl_price:
                    pnl = (sl_price - entry) * lot_contract
                    balance += pnl
                    trades.append((times[i], "BUY", "SL", pnl, balance))
                    position = None
                elif hi >= tp_price:
                    pnl = (tp_price - entry) * lot_contract
                    balance += pnl
                    trades.append((times[i], "BUY", "TP", pnl, balance))
                    position = None
            else:  # sell
                if hi >= sl_price:
                    pnl = (entry - sl_price) * lot_contract
                    balance += pnl
                    trades.append((times[i], "SELL", "SL", pnl, balance))
                    position = None
                elif lo <= tp_price:
                    pnl = (entry - tp_price) * lot_contract
                    balance += pnl
                    trades.append((times[i], "SELL", "TP", pnl, balance))
                    position = None

        if position is None:
            ef = ema_f[i]
            es = ema_s[i]
            rv = rsi[i]
            if np.isnan(ef) or np.isnan(es) or np.isnan(rv):
                continue
            if sess_arr is not None and sess_arr[i] not in sessions:
                continue
            if vol_ok_arr is not None and not vol_ok_arr[i]:
                continue

            if (direction in ("buy", "both")) and ef > es and rv < rsi_oversold:
                entry = price
                dirn = 0
                sl_price = price - sl_pts * pip
                tp_price = price + tp_pts * pip
                position = True
                balance -= cost
            elif (direction in ("sell", "both")) and ef < es and rv > rsi_overbought:
                entry = price
                dirn = 1
                sl_price = price + sl_pts * pip
                tp_price = price - tp_pts * pip
                position = True
                balance -= cost

    return trades, balance


def _run_auto_discover(session_id: str, config: dict):
    from backtest import fetch_data, INSTRUMENT, STRATEGIES

    mt5_api_url = config["mt5_api_url"]
    symbol = config["symbol"]
    days = config["days"]
    direction = config.get("direction", "both")
    min_trades = config.get("min_trades", 20)
    volume_filter = config.get("volume_filter", False)
    use_spread = config.get("use_spread", True)
    sessions = [s.strip() for s in config.get("sessions_filter", "").split(",") if s.strip()] or None
    sl_min, sl_max, sl_step, tp_min, tp_max, tp_step = _get_sl_tp_ranges(symbol)
    cancel = lambda: research_sessions.get(session_id, {}).get("cancelled", False)

    # Fetch data using first strategy that works
    strat = next(iter(STRATEGIES.values()))
    print(f"[AutoDiscover] Fetching {days} days of {symbol}...")
    global_log(f"[AutoDiscover] Avvio ricerca automatica per {symbol} ({days} giorni), walk-forward 70/30, min_trade={min_trades}", file=AUTO_LOG_FILE)
    try:
        dfs = fetch_data(symbol, strat, days, mt5_api_url)
    except Exception as e:
        with research_lock:
            if session_id in research_sessions:
                research_sessions[session_id]["status"] = "error"
                research_sessions[session_id]["result"] = {"error": f"Impossibile scaricare dati per {symbol}: {e}"}
        return

    df = dfs.get("m15") or dfs.get("m1") or dfs.get("m5")
    if df is None:
        with research_lock:
            if session_id in research_sessions:
                research_sessions[session_id]["status"] = "error"
                research_sessions[session_id]["result"] = {"error": f"Nessun timeframe disponibile per {symbol}"}
        return

    instr = INSTRUMENT.get(symbol, {"pip": 0.01, "contract": 100})
    pip = instr["pip"]
    contract = instr["contract"]
    spread_pips = SPREAD_PIPS.get(symbol.upper(), 0.0) if use_spread else 0.0
    target = config.get("target_return", 30.0)
    initial = config["balance"]

    # Walk-forward split 70/30 (in-sample per ottimizzare, out-of-sample per validare)
    split = max(1, int(len(df) * 0.70))
    df_is = df.iloc[:split].reset_index(drop=True)
    df_oos = df.iloc[split:].reset_index(drop=True)
    global_log(
        f"[AutoDiscover] Split walk-forward: {len(df_is)} bar in-sample, {len(df_oos)} bar out-of-sample, spread={spread_pips} pips",
        file=AUTO_LOG_FILE,
    )

    # Parameter grid
    ema_fast_vals = [5, 9, 15]
    ema_slow_vals = [15, 30, 50]
    rsi_oversold_vals = [35, 40, 45]
    rsi_overbought_vals = [55, 60, 65]
    sl_range = list(range(sl_min, sl_max + 1, sl_step))
    tp_range = list(range(tp_min, tp_max + 1, tp_step))
    directions = ["buy", "sell"] if direction == "both" else [direction]

    all_combos = [(ef, es, ro, rb, sl, tp, d)
                  for ef in ema_fast_vals
                  for es in ema_slow_vals if es > ef
                  for ro in rsi_oversold_vals
                  for rb in rsi_overbought_vals if rb > ro
                  for sl in sl_range
                  for tp in tp_range if tp > sl
                  for d in directions]

    # Precalcola indicatori e filtri una sola volta per split (grande speedup)
    ema_periods = set(ema_fast_vals) | set(ema_slow_vals)
    cache_is = _prepare_split_cache(df_is, ema_periods, {14}, volume_filter, sessions)
    cache_oos = _prepare_split_cache(df_oos, ema_periods, {14}, volume_filter, sessions)

    total = len(all_combos)
    is_results = []

    # FASE 1: ottimizzazione sul campione in-sample
    for i, (ef, es, ro, rb, sl, tp, dirn) in enumerate(all_combos):
        if cancel():
            with research_lock:
                if session_id in research_sessions:
                    research_sessions[session_id]["status"] = "cancelled"
            return

        try:
            trades, final_bal = _run_generic_backtest(
                df_is, ef, es, 14, ro, rb, sl, tp, dirn,
                config["lot"], initial, pip, contract,
                spread_pips=spread_pips, volume_filter=volume_filter, sessions=sessions,
                ema_cache=cache_is[0], rsi_arr=None, vol_ok_arr=cache_is[2], sess_arr=cache_is[3], rsi_cache=cache_is[1],
            )
            metrics = _summarize_trades(trades, initial)
            if metrics["trades"] < min_trades:
                continue
            is_results.append((metrics, (ef, es, ro, rb, sl, tp, dirn)))
        except Exception as e:
            logging.error(f"[AutoDiscover] EMA{ef}/{es} RSI<{ro} RSI>{rb} SL={sl} TP={tp}: {e}")
            global_log(f"[AutoDiscover] Errore EMA{ef}/{es} RSI<{ro} RSI>{rb} SL={sl} TP={tp}: {e}", file=AUTO_LOG_FILE)
            continue

        pct = int((i + 1) / total * 100)
        with research_lock:
            if session_id in research_sessions:
                research_sessions[session_id]["progress"] = pct

    # Prendi i migliori in-sample (per rendimento) e validali out-of-sample
    is_results.sort(key=lambda x: -x[0]["return_pct"])
    top = is_results[:15]

    results = []
    for metrics, (ef, es, ro, rb, sl, tp, dirn) in top:
        try:
            trades_oos, final_bal_oos = _run_generic_backtest(
                df_oos, ef, es, 14, ro, rb, sl, tp, dirn,
                config["lot"], initial, pip, contract,
                spread_pips=spread_pips, volume_filter=volume_filter, sessions=sessions,
                ema_cache=cache_oos[0], rsi_arr=None, vol_ok_arr=cache_oos[2], sess_arr=cache_oos[3], rsi_cache=cache_oos[1],
            )
            oos = _summarize_trades(trades_oos, initial)
        except Exception as e:
            logging.error(f"[AutoDiscover] OOS EMA{ef}/{es} SL={sl} TP={tp}: {e}")
            continue

        label = f"EMA{ef}/{es} RSI<{ro} RSI>{rb}"
        results.append({
            "label": label,
            "ema_fast": int(ef), "ema_slow": int(es),
            "rsi_oversold": int(ro), "rsi_overbought": int(rb),
            "sl": int(sl), "tp": int(tp), "direction": dirn,
            # in-sample
            "trades": int(metrics["trades"]),
            "win_rate": float(round(metrics["win_rate"], 1)),
            "return_pct": float(round(metrics["return_pct"], 1)),
            "max_dd": float(round(metrics["max_dd"], 1)),
            "sharpe": float(round(metrics["sharpe"], 2)),
            # out-of-sample (validazione)
            "oos_trades": int(oos["trades"]),
            "oos_win_rate": float(round(oos["win_rate"], 1)),
            "oos_return_pct": float(round(oos["return_pct"], 1)),
            "oos_max_dd": float(round(oos["max_dd"], 1)),
            "oos_sharpe": float(round(oos["sharpe"], 2)),
            "target_hit": bool(oos["return_pct"] >= target),
        })

    results.sort(key=lambda r: -(r["oos_return_pct"] if r["oos_trades"] else -999))
    target_hits = [r for r in results if r["target_hit"]]

    with research_lock:
        if session_id in research_sessions:
            if research_sessions[session_id]["cancelled"]:
                research_sessions[session_id]["status"] = "cancelled"
            elif not results:
                research_sessions[session_id]["status"] = "error"
                research_sessions[session_id]["result"] = {
                    "error": f"Nessuna combinazione ha raggiunto il minimo di {min_trades} trade per {symbol}."
                }
            else:
                research_sessions[session_id]["status"] = "done"
                research_sessions[session_id]["result"] = {
                    "results": results,
                    "target_hits": [r["label"] for r in target_hits],
                    "target_return": target,
                }

    global_log(f"[AutoDiscover] Fine ricerca per {symbol}: {len(is_results)} combinazioni sopra min_trade, {len(results)} validate OOS, {len(target_hits)} sopra il target", file=AUTO_LOG_FILE)


# ── Agent Discover (LLM-guided search) ─────────────────────────

class AgentDiscoverRequest(BaseModel):
    symbol: str
    days: int = 90
    lot: float = 0.01
    balance: float = 1000
    direction: str = "both"
    target_return: float = 30.0
    min_trades: int = 20
    volume_filter: bool = False
    sessions_filter: str = ""
    use_spread: bool = True
    iterations: int = 4
    batch_size: int = 6
    model: str = "openrouter/free"


def _agent_bounds(symbol: str):
    sl_min, sl_max, _sl_step, tp_min, tp_max, _tp_step = _get_sl_tp_ranges(symbol)
    return {
        "ema_fast": (3, 30),
        "ema_slow": (10, 100),
        "rsi_period": (7, 21),
        "rsi_oversold": (20, 48),
        "rsi_overbought": (52, 80),
        "sl": (sl_min, sl_max),
        "tp": (tp_min, tp_max),
    }


def _random_agent_config(bounds, rng, direction):
    ef = rng.randint(*bounds["ema_fast"])
    es = rng.randint(max(ef + 2, bounds["ema_slow"][0]), bounds["ema_slow"][1])
    ro = rng.randint(*bounds["rsi_oversold"])
    rb = rng.randint(max(ro + 4, bounds["rsi_overbought"][0]), bounds["rsi_overbought"][1])
    sl = rng.randint(*bounds["sl"])
    tp = rng.randint(max(sl + 1, bounds["tp"][0]), bounds["tp"][1])
    return {
        "ema_fast": int(ef), "ema_slow": int(es),
        "rsi_period": int(rng.randint(*bounds["rsi_period"])),
        "rsi_oversold": int(ro), "rsi_overbought": int(rb),
        "sl": int(sl), "tp": int(tp),
        "direction": direction if direction != "both" else rng.choice(["buy", "sell"]),
    }


def _sanitize_agent_config(raw, bounds, direction):
    def _clamp(v, lo, hi, default):
        try:
            return max(int(lo), min(int(hi), int(v)))
        except (TypeError, ValueError):
            return default

    ef = _clamp(raw.get("ema_fast"), *bounds["ema_fast"], 9)
    es = _clamp(raw.get("ema_slow"), max(ef + 2, bounds["ema_slow"][0]), bounds["ema_slow"][1], 30)
    rsi_p = _clamp(raw.get("rsi_period"), *bounds["rsi_period"], 14)
    ro = _clamp(raw.get("rsi_oversold"), *bounds["rsi_oversold"], 35)
    rb = _clamp(raw.get("rsi_overbought"), max(ro + 2, bounds["rsi_overbought"][0]), bounds["rsi_overbought"][1], 65)
    sl = _clamp(raw.get("sl"), *bounds["sl"], bounds["sl"][0])
    tp = _clamp(raw.get("tp"), max(sl + 1, bounds["tp"][0]), bounds["tp"][1], bounds["tp"][0] + 100)
    d = str(raw.get("direction", direction)).lower()
    if d not in ("buy", "sell", "both"):
        d = direction
    return {
        "ema_fast": ef, "ema_slow": es, "rsi_period": rsi_p,
        "rsi_oversold": ro, "rsi_overbought": rb, "sl": sl, "tp": tp, "direction": d,
    }


def _config_key(c):
    return (c["ema_fast"], c["ema_slow"], c["rsi_period"], c["rsi_oversold"], c["rsi_overbought"], c["sl"], c["tp"], c["direction"])


def _llm_propose_configs(prompt, batch_size, model):
    """Chiama l'LLM (OpenRouter) e chiede JSON {reasoning, configs}. Ritorna (reasoning, configs|None)."""
    import os
    import json
    import re

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        global_log("[AgentDiscover] OPENROUTER_API_KEY mancante, fallback a proposte casuali", file=AUTO_LOG_FILE)
        return None, None

    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        content = response.choices[0].message.content or ""
    except Exception as e:
        logging.error(f"[AgentDiscover] Errore LLM: {e}")
        global_log(f"[AgentDiscover] Errore LLM: {e}", file=AUTO_LOG_FILE)
        return None, None

    try:
        content = content.strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
        if fence:
            content = fence.group(1)
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("JSON non trovato")
        parsed = json.loads(content[start:end + 1])
        reasoning = str(parsed.get("reasoning", ""))[:1500]
        configs = parsed.get("configs") or []
        return reasoning, configs
    except Exception as e:
        logging.error(f"[AgentDiscover] Risposta LLM non valida ({e}): {content[:300]}")
        return None, None


def _run_agent_discover(session_id: str, config: dict):
    import random
    from backtest import fetch_data, INSTRUMENT, STRATEGIES

    mt5_api_url = config["mt5_api_url"]
    symbol = config["symbol"]
    days = config["days"]
    direction = config.get("direction", "both")
    min_trades = config.get("min_trades", 20)
    volume_filter = config.get("volume_filter", False)
    use_spread = config.get("use_spread", True)
    sessions = [s.strip() for s in config.get("sessions_filter", "").split(",") if s.strip()] or None
    iterations = max(1, config.get("iterations", 4))
    batch_size = max(2, config.get("batch_size", 6))
    model = config.get("model", "openrouter/free")
    cancel = lambda: research_sessions.get(session_id, {}).get("cancelled", False)

    from backtest import STRATEGIES
    strat = next(iter(STRATEGIES.values()))
    print(f"[AgentDiscover] Fetching {days} days of {symbol}...")
    global_log(f"[AgentDiscover] Avvio agente AI per {symbol} ({days} giorni), {iterations} iterazioni x {batch_size}", file=AUTO_LOG_FILE)
    try:
        dfs = fetch_data(symbol, strat, days, mt5_api_url)
    except Exception as e:
        with research_lock:
            if session_id in research_sessions:
                research_sessions[session_id]["status"] = "error"
                research_sessions[session_id]["result"] = {"error": f"Impossibile scaricare dati per {symbol}: {e}"}
        return

    df = dfs.get("m15") or dfs.get("m1") or dfs.get("m5")
    if df is None:
        with research_lock:
            if session_id in research_sessions:
                research_sessions[session_id]["status"] = "error"
                research_sessions[session_id]["result"] = {"error": f"Nessun timeframe disponibile per {symbol}"}
        return

    instr = INSTRUMENT.get(symbol, {"pip": 0.01, "contract": 100})
    pip = instr["pip"]
    contract = instr["contract"]
    spread_pips = SPREAD_PIPS.get(symbol.upper(), 0.0) if use_spread else 0.0
    target = config.get("target_return", 30.0)
    initial = config["balance"]
    bounds = _agent_bounds(symbol)

    split = max(1, int(len(df) * 0.70))
    df_is = df.iloc[:split].reset_index(drop=True)
    df_oos = df.iloc[split:].reset_index(drop=True)
    ema_periods = set(range(bounds["ema_fast"][0], bounds["ema_slow"][1] + 1))
    rsi_periods = set(range(bounds["rsi_period"][0], bounds["rsi_period"][1] + 1))
    cache_is = _prepare_split_cache(df_is, ema_periods, rsi_periods, volume_filter, sessions)
    cache_oos = _prepare_split_cache(df_oos, ema_periods, rsi_periods, volume_filter, sessions)

    def _eval(c):
        trades, _ = _run_generic_backtest(
            df_is, c["ema_fast"], c["ema_slow"], c["rsi_period"],
            c["rsi_oversold"], c["rsi_overbought"], c["sl"], c["tp"], c["direction"],
            config["lot"], initial, pip, contract,
            spread_pips=spread_pips, volume_filter=volume_filter, sessions=sessions,
            ema_cache=cache_is[0], rsi_cache=cache_is[1], vol_ok_arr=cache_is[2], sess_arr=cache_is[3],
        )
        m_is = _summarize_trades(trades, initial)
        if m_is["trades"] < min_trades:
            return None, None
        trades_oos, _ = _run_generic_backtest(
            df_oos, c["ema_fast"], c["ema_slow"], c["rsi_period"],
            c["rsi_oversold"], c["rsi_overbought"], c["sl"], c["tp"], c["direction"],
            config["lot"], initial, pip, contract,
            spread_pips=spread_pips, volume_filter=volume_filter, sessions=sessions,
            ema_cache=cache_oos[0], rsi_cache=cache_oos[1], vol_ok_arr=cache_oos[2], sess_arr=cache_oos[3],
        )
        m_oos = _summarize_trades(trades_oos, initial)
        return m_is, m_oos

    rng = random.Random()
    tested = {}
    results = []
    best_oos = -1e9
    stale_rounds = 0
    reasoning = ""
    evals_done = 0
    total_evals = iterations * batch_size

    def _add(c, m_is, m_oos):
        nonlocal best_oos, stale_rounds
        key = _config_key(c)
        tested[key] = True
        label = f"EMA{c['ema_fast']}/{c['ema_slow']} RSI{c['rsi_period']}<{c['rsi_oversold']} RSI>{c['rsi_overbought']}"
        row = {
            "label": label,
            "ema_fast": int(c["ema_fast"]), "ema_slow": int(c["ema_slow"]),
            "rsi_period": int(c["rsi_period"]),
            "rsi_oversold": int(c["rsi_oversold"]), "rsi_overbought": int(c["rsi_overbought"]),
            "sl": int(c["sl"]), "tp": int(c["tp"]), "direction": c["direction"],
            "trades": int(m_is["trades"]),
            "win_rate": float(round(m_is["win_rate"], 1)),
            "return_pct": float(round(m_is["return_pct"], 1)),
            "max_dd": float(round(m_is["max_dd"], 1)),
            "sharpe": float(round(m_is["sharpe"], 2)),
            "oos_trades": int(m_oos["trades"]),
            "oos_win_rate": float(round(m_oos["win_rate"], 1)),
            "oos_return_pct": float(round(m_oos["return_pct"], 1)),
            "oos_max_dd": float(round(m_oos["max_dd"], 1)),
            "oos_sharpe": float(round(m_oos["sharpe"], 2)),
            "target_hit": bool(m_oos["return_pct"] >= target),
        }
        results.append(row)
        if row["oos_trades"] >= max(3, min_trades // 2) and row["oos_return_pct"] > best_oos:
            best_oos = row["oos_return_pct"]
            stale_rounds = 0
        else:
            stale_rounds += 1
        return row

    # FASE 1: seed random (per dare all'LLM dati reali da analizzare)
    seed_pool = []
    while len(seed_pool) < batch_size:
        c = _random_agent_config(bounds, rng, direction)
        if _config_key(c) not in tested:
            seed_pool.append(c)

    for round_idx in range(iterations):
        if cancel():
            with research_lock:
                if session_id in research_sessions:
                    research_sessions[session_id]["status"] = "cancelled"
            return

        # Proposte: prima iterazione = seed, poi LLM (o random se fallisce)
        proposals = []
        if round_idx == 0:
            proposals = seed_pool
        else:
            hist = sorted(results, key=lambda r: -r["oos_return_pct"])[:15]
            hist_txt = "\n".join(
                f"- EMA{r['ema_fast']}/{r['ema_slow']} RSI{r.get('rsi_period',14)}<{r['rsi_oversold']} >{r['rsi_overbought']} "
                f"{r['direction']} SL={r['sl']} TP={r['tp']}: IS {r['return_pct']:+.1f}% ({r['trades']}t, WR {r['win_rate']:.0f}%, DD {r['max_dd']:.1f}%) | "
                f"OOS {r['oos_return_pct']:+.1f}% ({r['oos_trades']}t, WR {r['oos_win_rate']:.0f}%)"
                for r in hist
            )
            prompt = f"""Sei un quant researcher che ottimizza strategie di trading con validazione walk-forward.
Stai cercando parametri per la strategia EMA + RSI su {symbol} ({days} giorni, split 70/30 con costi spread {'ATTIVI' if use_spread else 'DISATTIVI'}).
Balance iniziale ${initial}, lotto {config['lot']}, minimo {min_trades} trade in-sample.

## BOUNDS PARAMETRI (rispettali rigorosamente)
- ema_fast: {bounds['ema_fast'][0]}-{bounds['ema_fast'][1]}
- ema_slow: {bounds['ema_slow'][0]}-{bounds['ema_slow'][1]} (SEMPRE > ema_fast)
- rsi_period: {bounds['rsi_period'][0]}-{bounds['rsi_period'][1]}
- rsi_oversold: {bounds['rsi_oversold'][0]}-{bounds['rsi_oversold'][1]}
- rsi_overbought: {bounds['rsi_overbought'][0]}-{bounds['rsi_overbought'][1]} (SEMPRE > oversold)
- sl: {bounds['sl'][0]}-{bounds['sl'][1]} punti
- tp: {bounds['tp'][0]}-{bounds['tp'][1]} punti (SEMPRE > sl)
- direction: buy oppure sell oppure both

## RISULTATI GIA' TESTATI (migliori per return OOS)
{hist_txt}

## OBIETTIVO
Analizza cosa ha funzionato in-sample e cosa NON ha retto out-of-sample (differenza IS vs OOS = overfitting).
Proponi {batch_size} NUOVE configurazioni, diverse tra loro e NON già testate, che secondo te potrebbero
reggere meglio OOS. Varia i parametri con intelligenza (es. SL più stretto se OOS perde molto, direzione diversa
se una direzione domina, RSI più estremo per meno trade ma più puliti, ecc.).

Rispondi SOLO con JSON valido in questo formato esatto:
{{"reasoning": "breve spiegazione in italiano (max 200 parole)", "configs": [{{"ema_fast": 9, "ema_slow": 50, "rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65, "sl": 200, "tp": 900, "direction": "buy"}}]}}"""
            llm_reasoning, raw_cfgs = _llm_propose_configs(prompt, batch_size, model)
            if raw_cfgs:
                reasoning = llm_reasoning or reasoning
                for rc in raw_cfgs:
                    c = _sanitize_agent_config(rc if isinstance(rc, dict) else {}, bounds, direction)
                    if _config_key(c) not in tested:
                        proposals.append(c)
            if len(proposals) < batch_size:
                while len(proposals) < batch_size:
                    c = _random_agent_config(bounds, rng, direction)
                    if _config_key(c) not in tested:
                        proposals.append(c)

        for c in proposals:
            if cancel():
                with research_lock:
                    if session_id in research_sessions:
                        research_sessions[session_id]["status"] = "cancelled"
                return
            try:
                m_is, m_oos = _eval(c)
                if m_is is None:
                    tested[_config_key(c)] = True
                    continue
                _add(c, m_is, m_oos)
            except Exception as e:
                logging.error(f"[AgentDiscover] Errore EMA{c['ema_fast']}/{c['ema_slow']} SL={c['sl']} TP={c['tp']}: {e}")
                continue
            finally:
                evals_done += 1
                pct = int(evals_done / total_evals * 100)
                with research_lock:
                    if session_id in research_sessions:
                        research_sessions[session_id]["progress"] = pct

        global_log(f"[AgentDiscover] Round {round_idx + 1}/{iterations}: {len(results)} config valide finora, best OOS {best_oos:+.2f}%", file=AUTO_LOG_FILE)

        if round_idx > 0 and stale_rounds >= 2 * batch_size:
            global_log("[AgentDiscover] Nessun miglioramento, stop anticipato", file=AUTO_LOG_FILE)
            break

    results.sort(key=lambda r: -(r["oos_return_pct"] if r["oos_trades"] else -999))
    target_hits = [r for r in results if r["target_hit"]]

    with research_lock:
        if session_id in research_sessions:
            if research_sessions[session_id]["cancelled"]:
                research_sessions[session_id]["status"] = "cancelled"
            elif not results:
                research_sessions[session_id]["status"] = "error"
                research_sessions[session_id]["result"] = {
                    "error": f"Nessuna configurazione ha raggiunto il minimo di {min_trades} trade per {symbol}."
                }
            else:
                research_sessions[session_id]["status"] = "done"
                research_sessions[session_id]["result"] = {
                    "results": results,
                    "target_hits": [r["label"] for r in target_hits],
                    "target_return": target,
                    "mode": "agent",
                    "analysis": reasoning,
                    "iterations_used": min(round_idx + 1, iterations),
                    "tested_count": len(tested),
                }

    global_log(f"[AgentDiscover] Fine agente per {symbol}: {len(results)} config valide, {len(tested)} testate, best OOS {best_oos:+.2f}%", file=AUTO_LOG_FILE)


@router.post("/signal-research/agent-discover")
def start_agent_discover(req: AgentDiscoverRequest):
    mt5_api_url = None
    from db import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT ss.ip, ss.port
            FROM traders t
            JOIN servers ss ON ss.id = t.slave_server_id
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row and row[0] and row[1]:
            mt5_api_url = f"http://{row[0]}:{row[1]}"
    finally:
        cursor.close()
        conn.close()

    if not mt5_api_url:
        return JSONResponse(status_code=400, content={"error": "No MT5 API URL found"})

    session_id = str(uuid.uuid4())[:8]
    config = {
        "symbol": req.symbol,
        "days": req.days,
        "lot": req.lot,
        "balance": req.balance,
        "direction": req.direction,
        "target_return": req.target_return,
        "min_trades": req.min_trades,
        "volume_filter": req.volume_filter,
        "sessions_filter": req.sessions_filter,
        "use_spread": req.use_spread,
        "iterations": req.iterations,
        "batch_size": req.batch_size,
        "model": req.model,
        "mt5_api_url": mt5_api_url,
    }

    with research_lock:
        research_sessions[session_id] = {
            "status": "running",
            "result": None,
            "cancelled": False,
            "progress": 0,
            "config": config,
        }

    t = threading.Thread(target=_run_agent_discover, args=(session_id, config), daemon=True)
    t.start()
    return {"session_id": session_id}


@router.post("/signal-research/auto-discover")
def start_auto_discover(req: AutoDiscoverRequest):
    mt5_api_url = None
    from db import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT ss.ip, ss.port
            FROM traders t
            JOIN servers ss ON ss.id = t.slave_server_id
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row and row[0] and row[1]:
            mt5_api_url = f"http://{row[0]}:{row[1]}"
    finally:
        cursor.close()
        conn.close()

    if not mt5_api_url:
        return JSONResponse(status_code=400, content={"error": "No MT5 API URL found"})

    session_id = str(uuid.uuid4())[:8]
    config = {
        "symbol": req.symbol,
        "days": req.days,
        "lot": req.lot,
        "balance": req.balance,
        "direction": req.direction,
        "target_return": req.target_return,
        "mt5_api_url": mt5_api_url,
    }

    with research_lock:
        research_sessions[session_id] = {
            "status": "running",
            "result": None,
            "cancelled": False,
            "progress": 0,
            "config": config,
        }

    t = threading.Thread(target=_run_auto_discover, args=(session_id, config), daemon=True)
    t.start()
    return {"session_id": session_id}


# ── PDF Export ──────────────────────────────────────────────────

class PdfExportRequest(BaseModel):
    symbol: str
    days: int
    lot: float
    balance: float
    sl_min: int
    sl_max: int
    sl_step: int
    tp_min: int
    tp_max: int
    tp_step: int
    strategies: List[str]
    results: List[dict]


@router.post("/signal-research/export-pdf")
def export_pdf(req: PdfExportRequest):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Signal Research Report", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120)
    from datetime import datetime
    pdf.cell(0, 6, f"Generato: {datetime.now().strftime('%d/%m/%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)

    # Parameters
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Parametri di Ricerca", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    params = [
        ("Symbol", req.symbol),
        ("Days", str(req.days)),
        ("Lot", str(req.lot)),
        ("Balance", f"${req.balance}"),
        ("SL Range", f"{req.sl_min} - {req.sl_max} (step {req.sl_step})"),
        ("TP Range", f"{req.tp_min} - {req.tp_max} (step {req.tp_step})"),
        ("Strategie", ", ".join(req.strategies)),
        ("Combinazioni testate", str(len(req.results))),
    ]
    for label, value in params:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(45, 6, f"{label}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    # Top 3 table
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Top 3 Risultati Migliori", new_x="LMARGIN", new_y="NEXT")

    headers = ["#", "Strategy", "SL", "TP", "Dir", "Trades", "Win%", "Return%", "MaxDD%", "Sharpe"]
    col_w = [8, 32, 16, 16, 14, 16, 18, 20, 20, 16]

    pdf.set_fill_color(50, 50, 50)
    pdf.set_text_color(255)
    pdf.set_font("Helvetica", "B", 9)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_text_color(0)
    pdf.set_font("Helvetica", "", 9)

    top3 = req.results[:3]
    medal_colors = [(255, 248, 220), (240, 240, 240), (255, 240, 230)]

    for idx, r in enumerate(top3):
        bg = medal_colors[idx] if idx < 3 else (255, 255, 255)
        pdf.set_fill_color(*bg)
        vals = [
            str(idx + 1), r.get("strategy", ""), str(r.get("sl", "")),
            str(r.get("tp", "")), r.get("direction", "?").upper(), str(r.get("trades", "")),
            f"{r.get('win_rate', 0):.1f}",
            f"{r.get('return_pct', 0):+.1f}",
            f"{r.get('max_dd', 0):.1f}",
            f"{r.get('sharpe', 0):.2f}",
        ]
        for i, v in enumerate(vals):
            pdf.cell(col_w[i], 7, v, border=1, fill=True, align="C")
        pdf.ln()

    # Recommendation
    if top3:
        best = top3[0]
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 10)
        if best.get("sharpe", 0) >= 1.5 and best.get("return_pct", 0) >= 10:
            pdf.set_text_color(0, 130, 0)
            rec = f"Forte raccomandazione: {best['strategy']} SL={best['sl']} TP={best['tp']}"
        elif best.get("sharpe", 0) >= 1 and best.get("return_pct", 0) >= 5:
            pdf.set_text_color(0, 100, 200)
            rec = f"Raccomandata: {best['strategy']} SL={best['sl']} TP={best['tp']}"
        else:
            pdf.set_text_color(200, 0, 0)
            rec = f"Usa con cautela: {best['strategy']} SL={best['sl']} TP={best['tp']}"
        pdf.cell(0, 7, rec, new_x="LMARGIN", new_y="NEXT")

    # Stream PDF back
    pdf_bytes = pdf.output()
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=signal_research_{req.symbol}_{req.days}d.pdf"},
    )


# ── Auto Discover PDF Export ──────────────────────────────────

class AutoDiscoverPdfRequest(BaseModel):
    symbol: str
    days: int
    lot: float
    balance: float
    target_return: float
    direction: str
    results: List[dict]


@router.post("/signal-research/auto-discover/export-pdf")
def export_auto_discover_pdf(req: AutoDiscoverPdfRequest):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Auto Strategy Discovery Report", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120)
    from datetime import datetime
    pdf.cell(0, 6, f"Generato: {datetime.now().strftime('%d/%m/%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)

    # Parameters
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Parametri di Ricerca", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    params = [
        ("Symbol", req.symbol),
        ("Days", str(req.days)),
        ("Lot", str(req.lot)),
        ("Balance", f"${req.balance}"),
        ("Direction", req.direction),
        ("Target Return", f"{req.target_return}%"),
        ("Combinazioni mostrate", str(len(req.results))),
    ]
    for label, value in params:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(45, 6, f"{label}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    # Top 10 table
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Top 10 Risultati Migliori", new_x="LMARGIN", new_y="NEXT")

    headers = ["#", "Label", "SL", "TP", "Dir", "Trades", "Win%", "Ret IS%", "Ret OOS%", "MaxDD%", "Sharpe"]
    col_w = [8, 36, 12, 12, 12, 14, 14, 18, 18, 18, 14]

    pdf.set_fill_color(50, 50, 50)
    pdf.set_text_color(255)
    pdf.set_font("Helvetica", "B", 8)
    for i, h in enumerate(headers):
        pdf.cell(col_w[i], 7, h, border=1, fill=True, align="C")
    pdf.ln()
    pdf.set_text_color(0)
    pdf.set_font("Helvetica", "", 8)

    medal_colors = [(255, 248, 220), (240, 240, 240), (255, 240, 230)]

    for idx, r in enumerate(req.results):
        bg = medal_colors[idx] if idx < 3 else (255, 255, 255)
        pdf.set_fill_color(*bg)
        vals = [
            str(idx + 1), r.get("label", ""), str(r.get("sl", "")),
            str(r.get("tp", "")), r.get("direction", "?").upper(), str(r.get("trades", "")),
            f"{r.get('win_rate', 0):.1f}",
            f"{r.get('return_pct', 0):+.1f}",
            f"{r.get('oos_return_pct', r.get('return_pct', 0)):+.1f}",
            f"{r.get('max_dd', 0):.1f}",
            f"{r.get('sharpe', 0):.2f}",
        ]
        for i, v in enumerate(vals):
            pdf.cell(col_w[i], 7, v, border=1, fill=True, align="C")
        pdf.ln()

    # Recommendation
    if req.results:
        best = req.results[0]
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 10)
        if best.get("target_hit"):
            pdf.set_text_color(0, 130, 0)
            rec = f"Target raggiunto: {best['label']} SL={best['sl']} TP={best['tp']} -> OOS {best.get('oos_return_pct', best.get('return_pct', 0)):+.1f}%"
        elif best.get("sharpe", 0) >= 1:
            pdf.set_text_color(0, 100, 200)
            rec = f"Raccomandata: {best['label']} SL={best['sl']} TP={best['tp']}"
        else:
            pdf.set_text_color(200, 0, 0)
            rec = f"Usa con cautela: {best['label']} SL={best['sl']} TP={best['tp']}"
        pdf.cell(0, 7, rec, new_x="LMARGIN", new_y="NEXT")

    # Stream PDF back
    pdf_bytes = pdf.output()
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=auto_discover_{req.symbol}_{req.days}d.pdf"},
    )
