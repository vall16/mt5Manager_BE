import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from indicators.ta import compute_ema, compute_rsi, compute_macd, compute_atr, compute_hma
from decimal import Decimal

SYMBOL = "XAUUSD"
LOT = 0.01
M1_LOOKBACK = 100
M5_LOOKBACK = 30
M15_LOOKBACK = 50

SL_FIXED = 500
TP_FIXED = 600
ATR_THRESHOLD = 12
VOLUME_MULT = 1.3

trades = []
balance = 10000.0
position = None

def fetch_data(symbol, start, end):
    print(f"Scaricamento dati M1...")
    m1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start, end)
    if m1 is None or len(m1) == 0:
        raise ValueError("Nessun dato M1")
    df_m1 = pd.DataFrame(m1)
    df_m1["time"] = pd.to_datetime(df_m1["time"], unit="s")

    print(f"Scaricamento dati M5...")
    m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start, end)
    if m5 is None or len(m5) == 0:
        raise ValueError("Nessun dato M5")
    df_m5 = pd.DataFrame(m5)
    df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")

    print(f"Scaricamento dati M15...")
    m15 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, start, end)
    if m15 is None or len(m15) == 0:
        raise ValueError("Nessun dato M15")
    df_m15 = pd.DataFrame(m15)
    df_m15["time"] = pd.to_datetime(df_m15["time"], unit="s")

    return df_m1, df_m5, df_m15

def get_m5_atr(df_m5_window):
    tmp = df_m5_window.copy()
    tmp["prev_close"] = tmp["close"].shift(1)
    tmp["tr"] = tmp.apply(lambda r: max(
        r["high"] - r["low"],
        abs(r["high"] - r["prev_close"]),
        abs(r["low"] - r["prev_close"])
    ), axis=1)
    return tmp["tr"].rolling(14).mean().iloc[-1]

def get_indicators(df_m1_i, df_m5_i, df_m15_i):
    ema_fast = compute_ema(df_m1_i, 9).iloc[-1]
    ema_slow = compute_ema(df_m1_i, 21).iloc[-1]
    rsi_m1 = compute_rsi(df_m1_i, 14).iloc[-1]
    macd, macd_sig = compute_macd(df_m1_i)

    hma_m5 = compute_hma(df_m5_i).iloc[-1]
    hma_m5_prev = compute_hma(df_m5_i).iloc[-2]

    ema_m15 = compute_ema(df_m15_i, 50).iloc[-1]
    price_m15 = df_m15_i["close"].iloc[-1]
    trend_macro_up = price_m15 > ema_m15

    atr_series = compute_atr(df_m1_i)
    atr = atr_series.iloc[-1]
    volatilty_expansion = atr > atr_series.rolling(10).mean().iloc[-1]

    candle_body = abs(df_m1_i["close"].iloc[-1] - df_m1_i["open"].iloc[-1])
    is_spike = candle_body > (atr * 3)

    atr_m5_val = get_m5_atr(df_m5_i)

    volume_avg = df_m5_i["tick_volume"].rolling(20).mean().iloc[-1]
    volume_now = df_m5_i["tick_volume"].iloc[-1]
    volume_ok = volume_now > volume_avg * VOLUME_MULT if volume_avg > 0 else True

    return {
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "macd": macd.iloc[-1],
        "macd_sig": macd_sig.iloc[-1],
        "hma_m5": hma_m5,
        "hma_m5_prev": hma_m5_prev,
        "trend_macro_up": trend_macro_up,
        "rsi_m1": rsi_m1,
        "volatilty_expansion": volatilty_expansion,
        "is_spike": is_spike,
        "atr_m5_val": atr_m5_val,
        "volume_ok": volume_ok,
    }

def check_buy(ind):
    return all([
        ind["ema_fast"] > ind["ema_slow"],
        ind["macd"] > ind["macd_sig"],
        ind["hma_m5"] > ind["hma_m5_prev"],
        ind["trend_macro_up"],
        45 < ind["rsi_m1"] < 70,
        ind["volatilty_expansion"],
        not ind["is_spike"],
        ind["volume_ok"],
    ])

def check_sell(ind):
    return all([
        ind["ema_fast"] < ind["ema_slow"],
        ind["macd"] < ind["macd_sig"],
        ind["hma_m5"] < ind["hma_m5_prev"],
        not ind["trend_macro_up"],
        30 < ind["rsi_m1"] < 55,
        ind["volatilty_expansion"],
        not ind["is_spike"],
        ind["volume_ok"],
    ])

def run_backtest(df_m1, df_m5, df_m15):
    global balance, position

    for i in range(M1_LOOKBACK, len(df_m1)):
        row = df_m1.iloc[i]
        ts = row["time"]

        m1_window = df_m1.iloc[:i+1]

        last_m5_time = m1_window[m1_window["time"] <= ts]["time"] \
            .apply(lambda t: t - pd.Timedelta(minutes=t.minute % 5, seconds=t.second)) \
            .max()
        last_m15_time = m1_window[m1_window["time"] <= ts]["time"] \
            .apply(lambda t: t - pd.Timedelta(minutes=t.minute % 15, seconds=t.second)) \
            .max()

        m5_window = df_m5[df_m5["time"] <= last_m5_time]
        m15_window = df_m15[df_m15["time"] <= last_m15_time]

        if len(m5_window) < 20 or len(m15_window) < M15_LOOKBACK:
            continue

        ind = get_indicators(m1_window, m5_window, m15_window)

        if position:
            low = row["low"]
            high = row["high"]
            entry, direction, sl, tp = position

            if direction == "buy":
                if low <= sl:
                    loss = (sl - entry) * LOT * 100
                    balance += loss
                    print(f"{ts} BUY STOPPED @ {sl:.2f} | PnL: {loss:.2f} | Balance: {balance:.2f}")
                    trades.append({"time": ts, "type": "BUY", "exit": "SL", "pnl": loss, "balance": balance})
                    position = None
                elif high >= tp:
                    gain = (tp - entry) * LOT * 100
                    balance += gain
                    print(f"{ts} BUY TP @ {tp:.2f} | PnL: {gain:.2f} | Balance: {balance:.2f}")
                    trades.append({"time": ts, "type": "BUY", "exit": "TP", "pnl": gain, "balance": balance})
                    position = None
            else:
                if high >= sl:
                    loss = (entry - sl) * LOT * 100
                    balance += loss
                    print(f"{ts} SELL STOPPED @ {sl:.2f} | PnL: {loss:.2f} | Balance: {balance:.2f}")
                    trades.append({"time": ts, "type": "SELL", "exit": "SL", "pnl": loss, "balance": balance})
                    position = None
                elif low <= tp:
                    gain = (entry - tp) * LOT * 100
                    balance += gain
                    print(f"{ts} SELL TP @ {tp:.2f} | PnL: {gain:.2f} | Balance: {balance:.2f}")
                    trades.append({"time": ts, "type": "SELL", "exit": "TP", "pnl": gain, "balance": balance})
                    position = None

        if position is None:
            direction = None
            if check_buy(ind):
                direction = "buy"
            elif check_sell(ind):
                direction = "sell"

            if direction:
                price = row["close"]
                sl_points = SL_FIXED
                tp_points = TP_FIXED
                if ind["atr_m5_val"] <= ATR_THRESHOLD:
                    sl_points = 500
                    tp_points = 600
                pip = 0.01
                sl_price = price - (sl_points * pip) if direction == "buy" else price + (sl_points * pip)
                tp_price = price + (tp_points * pip) if direction == "buy" else price - (tp_points * pip)
                position = (price, direction, sl_price, tp_price)
                print(f"{ts} ENTRY {direction.upper()} @ {price:.2f} SL={sl_price:.2f} TP={tp_price:.2f} | ATR M5: {ind['atr_m5_val']:.1f} | VOL:{'OK' if ind['volume_ok'] else 'LOW'}")

    print(f"\nFinal balance: {balance:.2f}")

def summary():
    global balance

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]

    print("\n" + "=" * 60)
    print("BACKTEST SUMMARY")
    print("=" * 60)
    print(f"Total trades     : {len(trades)}")
    print(f"Wins             : {len(wins)}")
    print(f"Losses           : {len(losses)}")
    print(f"Win rate         : {len(wins)/len(trades)*100:.1f}%" if trades else "N/A")
    print(f"Gross profit     : {sum(t['pnl'] for t in wins):.2f}")
    print(f"Gross loss       : {sum(t['pnl'] for t in losses):.2f}")
    print(f"Net PnL          : {sum(t['pnl'] for t in trades):.2f}")
    print(f"Final balance    : {balance:.2f}")
    print(f"Return           : {(balance - 10000) / 10000 * 100:.1f}%")

    if wins:
        print(f"Avg win          : {sum(t['pnl'] for t in wins)/len(wins):.2f}")
    if losses:
        print(f"Avg loss         : {sum(t['pnl'] for t in losses)/len(losses):.2f}")

    if losses:
        win_loss_ratio = abs(sum(t['pnl'] for t in wins) / len(wins)) / abs(sum(t['pnl'] for t in losses) / len(losses)) if len(wins) > 0 and len(losses) > 0 else 0
        print(f"Win/Loss ratio   : {win_loss_ratio:.2f}")

    print("=" * 60)

    for t in trades[-10:]:
        print(f"{t['time']} | {t['type']:5} | {t['exit']:3} | PnL: {t['pnl']:7.2f} | Bal: {t['balance']:.2f}")


if __name__ == "__main__":
    import sys

    if not mt5.initialize():
        print("MT5 initialization failed")
        sys.exit(1)

    end = datetime.now()
    start = end - timedelta(days=90)

    df_m1, df_m5, df_m15 = fetch_data(SYMBOL, start, end)
    print(f"Dati: M1={len(df_m1)}, M5={len(df_m5)}, M15={len(df_m15)}")

    run_backtest(df_m1, df_m5, df_m15)
    summary()

    mt5.shutdown()
