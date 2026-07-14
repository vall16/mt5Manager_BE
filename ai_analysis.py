import os
import json
import requests
from decimal import Decimal
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

MT5_API_URL = os.environ.get("MT5_API_URL", "http://127.0.0.1:8081")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY"),
)
MODEL = "openrouter/free"


def fetch_trade_data(conn, trader_id, limit=100):
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT t.name, t.sl, tp, t.tsl, t.moltiplicatore, t.fix_lot,
               t.selected_signal, t.selected_symbol
        FROM traders t WHERE t.id = %s
    """, (trader_id,))
    trader = cursor.fetchone()

    cursor.execute("""
        SELECT symbol, type, volume, price_open, price_close,
               profit, opened_at, closed_at, comment
        FROM slave_orders
        WHERE trader_id = %s AND closed_at IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT %s
    """, (trader_id, limit))
    trades = cursor.fetchall()

    cursor.close()

    for t in trades:
        for k, v in t.items():
            if isinstance(v, Decimal):
                t[k] = float(v)

    return trader, trades


def fetch_trade_data_from_mt5(trader_id, days=30, limit=100):
    """Fetch closed trades from MT5 history + trader config from DB."""
    import requests
    from db import get_connection

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT t.name, t.sl, tp, t.tsl, t.moltiplicatore, t.fix_lot,
               t.selected_signal, t.selected_symbol,
               ss.ip AS slave_ip, ss.port AS slave_port
        FROM traders t
        LEFT JOIN servers ss ON t.slave_server_id = ss.id
        WHERE t.id = %s
    """, (trader_id,))
    trader = cursor.fetchone()

    cursor.execute("SELECT ticket FROM slave_orders WHERE trader_id = %s", (trader_id,))
    known_tickets = {row["ticket"] for row in cursor.fetchall()}
    cursor.close()
    conn.close()

    if not trader or not trader.get("slave_ip"):
        return trader, []

    slave_url = f"http://{trader['slave_ip']}:{trader['slave_port']}"
    resp = requests.get(f"{slave_url}/history", params={"days": days}, timeout=15)
    if resp.status_code != 200:
        return trader, []

    deals = resp.json().get("deals", [])
    trades = []
    for d in deals:
        if d.get("type") not in (0, 1):  # only BUY/SELL
            continue
        entry = d.get("entry", 0)
        if entry != 1:  # 1 = out (close), skip opening deals
            continue
        trades.append({
            "symbol": d.get("symbol", ""),
            "type": "BUY" if d.get("type") == 0 else "SELL",
            "volume": d.get("volume", 0),
            "price_open": d.get("price", 0),
            "price_close": d.get("price", 0),
            "profit": d.get("profit", 0),
            "opened_at": str(d.get("time", "")),
            "closed_at": str(d.get("time", "")),
            "comment": d.get("comment", ""),
        })

    trades.sort(key=lambda x: x["closed_at"], reverse=True)
    trades = trades[:limit]

    for t in trades:
        for k, v in t.items():
            if isinstance(v, Decimal):
                t[k] = float(v)

    return trader, trades


def compute_metrics(trades):
    if not trades:
        return {"error": "Nessun trade trovato"}

    total = len(trades)
    wins = [t for t in trades if t["profit"] and t["profit"] > 0]
    losses = [t for t in trades if t["profit"] and t["profit"] <= 0]

    total_profit = sum(t["profit"] for t in trades if t["profit"])
    avg_win = sum(t["profit"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["profit"] for t in losses) / len(losses) if losses else 0
    win_rate = len(wins) / total * 100

    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    peak = 0
    equity = 0
    max_drawdown = 0
    for t in reversed(trades):
        equity += t["profit"] or 0
        peak = max(peak, equity)
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)

    by_symbol = {}
    by_type = {"BUY": {"count": 0, "profit": 0}, "SELL": {"count": 0, "profit": 0}}
    for t in trades:
        sym = t["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {"count": 0, "wins": 0, "profit": 0}
        by_symbol[sym]["count"] += 1
        by_symbol[sym]["profit"] += t["profit"] or 0
        if t["profit"] and t["profit"] > 0:
            by_symbol[sym]["wins"] += 1

        tp = t["type"].upper()
        if tp in by_type:
            by_type[tp]["count"] += 1
            by_type[tp]["profit"] += t["profit"] or 0

    best = max(trades, key=lambda t: t["profit"] or 0)
    worst = min(trades, key=lambda t: t["profit"] or 0)

    last_20 = trades[:20]
    recent_wins = len([t for t in last_20 if t["profit"] and t["profit"] > 0])
    recent_win_rate = recent_wins / len(last_20) * 100

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "total_profit": round(total_profit, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "max_drawdown": round(max_drawdown, 2),
        "best_trade": {"symbol": best["symbol"], "type": best["type"],
                       "profit": best["profit"], "date": str(best["closed_at"])},
        "worst_trade": {"symbol": worst["symbol"], "type": worst["type"],
                        "profit": worst["profit"], "date": str(worst["closed_at"])},
        "by_symbol": by_symbol,
        "by_type": by_type,
        "recent_win_rate": round(recent_win_rate, 1),
    }


def build_prompt(trader, trades, metrics):
    recent = []
    for t in trades[:30]:
        recent.append({
            "symbol": t["symbol"],
            "type": t["type"],
            "volume": t["volume"],
            "open": t["price_open"],
            "close": t["price_close"],
            "profit": t["profit"],
            "opened": str(t["opened_at"]),
            "closed": str(t["closed_at"]),
        })

    prompt = f"""Sei un esperto analista di trading Forex/CFD. Analizza i seguenti dati e fornisci un report in italiano.

## CONFIGURAZIONE TRADER
- Nome: {trader.get('name', 'N/A')}
- Strategia: {trader.get('selected_signal', 'N/A')}
- Simbolo principale: {trader.get('selected_symbol', 'N/A')}
- Stop Loss: {trader.get('sl', 'N/A')}
- Take Profit: {trader.get('tp', 'N/A')}
- Trailing SL: {trader.get('tsl', 'N/A')}
- Moltiplicatore lotto: {trader.get('moltiplicatore', 'N/A')}

## METRICHE COMPLESSIVE
- Trade totali: {metrics['total_trades']}
- Vittorie/Sconfitte: {metrics['wins']}/{metrics['losses']}
- Win Rate: {metrics['win_rate']}%
- Win Rate ultimi 20 trade: {metrics['recent_win_rate']}%
- Profitto totale: ${metrics['total_profit']}
- Profitto medio per win: ${metrics['avg_win']}
- Loss media per loss: ${metrics['avg_loss']}
- Expectancy (guadagno medio per trade): ${metrics['expectancy']}
- Drawdown massimo: ${metrics['max_drawdown']}

## DISTRIBUZIONE PER SIMBOLO
{json.dumps(metrics['by_symbol'], indent=2)}

## DISTRIBUZIONE BUY vs SELL
{json.dumps(metrics['by_type'], indent=2)}

## MIGLIORE TRADE
{json.dumps(metrics['best_trade'], indent=2)}

## PEGGIORE TRADE
{json.dumps(metrics['worst_trade'], indent=2)}

## ULTIMI 30 TRADE (dal più recente)
{json.dumps(recent, indent=2)}

---

## OUTPUT RICHIESTO

Fornisci un report strutturato con:

1. **Riepilogo Performance** — analisi generale dei risultati
2. **Pattern Rilevati** — tendenze nei trade (es. "le BUY su XAUUSD hanno win rate X%")
3. **Punti di Forza** — cosa funziona bene
4. **Punti Deboli** — cosa non funziona
5. **Suggerimenti Concreti** — raccomandazioni specifiche su SL/TP, simboli, gestione del rischio
6. **Valutazione Strategia** — giudizio sulla strategia attuale e possibili miglioramenti

Sii specifico, cita i numeri, e dai consigli actionable."""

    return prompt


def analyze_with_qwen(prompt):
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def get_analysis(conn, trader_id, limit=100, source="db", days=30):
    if source == "mt5":
        trader, trades = fetch_trade_data_from_mt5(trader_id, days=days, limit=limit)
    else:
        trader, trades = fetch_trade_data(conn, trader_id, limit)
    if not trades:
        return {"error": "Nessun trade chiuso trovato per questo trader"}

    metrics = compute_metrics(trades)
    prompt = build_prompt(trader, trades, metrics)
    analysis = analyze_with_qwen(prompt)

    return {
        "trader_id": trader_id,
        "trader_name": trader.get("name"),
        "source": source,
        "metrics": metrics,
        "analysis": analysis,
    }
