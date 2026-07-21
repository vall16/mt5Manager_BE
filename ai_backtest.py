import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=__import__("os").environ.get("OPENROUTER_API_KEY"),
)
MODEL = "openrouter/free"


def _safe_json(data):
    return json.dumps(data, default=str, ensure_ascii=False)


def build_backtest_prompt(backtest_result, strategy_name, symbol, days, lot, balance, sl_tp_info=None):
    summary = backtest_result.get("summary", {})
    trades = backtest_result.get("trades", [])

    recent_trades = trades[-30:] if len(trades) > 30 else trades

    by_session = summary.get("by_session", {})
    by_day = summary.get("by_day", {})
    by_hour = summary.get("by_hour", {})
    by_direction = summary.get("by_direction", {})

    session_json = _safe_json(by_session)
    day_json = _safe_json(by_day)
    hour_json = _safe_json({str(k): v for k, v in list(by_hour.items())[:8]})
    direction_json = _safe_json(by_direction)
    trades_json = _safe_json([{"time": t.get("time"), "type": t.get("type"), "exit": t.get("exit"), "pnl": t.get("pnl")} for t in recent_trades])

    prompt = f"""Sei un esperto quant analyst e sviluppatore di strategie di trading. Analizza i risultati del backtest seguente e fornisci suggerimenti concreti per migliorare la strategia.

## CONFIGURAZIONE BACKTEST
- Strategia: {strategy_name}
- Simbolo: {symbol}
- Periodo: {days} giorni
- Lotto: {lot}
- Balance iniziale: ${balance}

## RISULTATI GENERALI
- Trade totali: {summary.get('total_trades', 0)}
- Trades/giorno: {summary.get('trades_per_day', 0)}
- Vittorie/Sconfitte: {summary.get('wins', 0)}/{summary.get('losses', 0)}
- Win Rate: {summary.get('win_rate', 0)}%
- Net PnL: ${summary.get('net_pnl', 0)}
- Return: {summary.get('return_pct', 0)}%
- Max Drawdown: ${summary.get('max_drawdown', 0)}
- Avg Win: ${summary.get('avg_win', 0)}
- Avg Loss: ${summary.get('avg_loss', 0)}
- Win/Loss Ratio: {summary.get('win_loss_ratio', 0)}

## PERFORMANCE PER SESSIONE
{session_json}

## PERFORMANCE PER GIORNO
{day_json}

## PERFORMANCE PER ORA (top 8)
{hour_json}

## PERFORMANCE BUY vs SELL
{direction_json}

## ULTIMI 30 TRADE
{trades_json}

---

## OUTPUT RICHIESTO

Analizza tutto e fornisci:

1. **Diagnosi** — perché la strategia performa così? Quali sono i problemi principali?

2. **Parametri SL/TP** — i valori attuali ({sl_tp_info}) sono ottimali? Suggerisci valori specifici basati sui dati.

3. **Filtro Sessione** — ci sono sessioni (ASIA/LONDON/NY-LON/NY/OFF) dove la strategia perde? Dovrebbe filtrarle?

4. **Filtro Direzione** — BUY o SELL performa meglio? Ha senso disattivarne una?

5. **Timing** — ci sono ore dove la strategia è particolarmente buona o cattiva?

6. **Parametri Indicatori** — suggerisci modifiche ai parametri della strategia (EMA periodi, RSI thresholds, etc.)

7. **Position Sizing** — con il balance attuale, quale lotto consigli per gestire il rischio?

8. **Codice Modificato** — se puoi, fornisci pezzi di codice Python con le modifiche suggerite (es. nuove condizioni buy/sell, filtri aggiuntivi).

Sii specifico, cita i numeri, e dai consigli ACTIONABLE che posso applicare subito."""

    return prompt


def analyze_backtest(backtest_result, strategy_name, symbol, days, lot, balance, sl_tp_info=None):
    prompt = build_backtest_prompt(backtest_result, strategy_name, symbol, days, lot, balance, sl_tp_info)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4000,
    )
    return response.choices[0].message.content
