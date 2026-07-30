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

router = APIRouter()

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
