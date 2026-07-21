import pandas as pd
import numpy as np
import argparse
import sys
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_signals_multi2 import STRATEGIES, Indicators
from indicators.ta import compute_ema, compute_rsi, compute_macd, compute_atr, compute_hma, compute_ichimoku

TIMEFRAME_MAP = {
    "m1": 1,
    "m5": 5,
    "m15": 15,
    "h1": 16385,
}

def _get_session_label(ts):
    try:
        dt = pd.Timestamp(ts).to_pydatetime().replace(tzinfo=ZoneInfo("Europe/Rome"))
    except Exception:
        return "UNKNOWN"
    h, m = dt.hour, dt.minute
    if 1 <= h < 9:
        return "ASIA"
    elif 9 <= h < 14:
        return "LONDON"
    elif 14 <= h < 17 or (h == 17 and m < 30):
        return "NY-LON"
    elif (h == 17 and m >= 30) or 18 <= h < 22:
        return "NY"
    else:
        return "OFF"

def fetch_rates(symbol, tf_key, n_candles, mt5_api_url, start_pos=0, retries=3):
    url = f"{mt5_api_url.rstrip('/')}/get_rates"
    payload = {"symbol": symbol, "timeframe": TIMEFRAME_MAP[tf_key], "n_candles": n_candles, "start_pos": start_pos}
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates", [])
            if not rates:
                return None
            dtype = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"), ("tick_volume", "i8")]
            return np.array([(r["time"], r["open"], r["high"], r["low"], r["close"], r.get("tick_volume", 0)) for r in rates], dtype=dtype)
        except Exception as e:
            print(f"    Attempt {attempt+1} failed: {e}")
            if attempt == retries - 1:
                return None
    return None

DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_DAYS = 30
DEFAULT_LOT = 0.01
DEFAULT_BALANCE = 10000.0
M1_LOOKBACK = 100
MAX_BARS = 40000

INSTRUMENT = {
    "XAUUSD":  {"pip": 0.01,    "contract": 100},
    "USDJPY":  {"pip": 0.01,    "contract": 1000},
    "GBPUSD":  {"pip": 0.0001, "contract": 100000},
    "GBPJPY":  {"pip": 0.01,   "contract": 1000},
    "AUDJPY":  {"pip": 0.01,   "contract": 1000},
    "EURUSD":  {"pip": 0.0001, "contract": 100000},
    "MSFT":    {"pip": 0.01,   "contract": 100},
    "MSFT.NAS": {"pip": 0.01,   "contract": 100},
    "NVDA":    {"pip": 0.01,   "contract": 100},
    "NVDA.NAS": {"pip": 0.01,   "contract": 100},
}

DEFAULT_SL = 500
DEFAULT_TP = 600


def fetch_rates_range(symbol, tf_key, date_from, date_to, mt5_api_url):
    url = f"{mt5_api_url.rstrip('/')}/get_rates_range"
    payload = {
        "symbol": symbol,
        "timeframe": TIMEFRAME_MAP[tf_key],
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates", [])
            if not rates:
                return None
            dtype = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"), ("tick_volume", "i8")]
            return np.array([(r["time"], r["open"], r["high"], r["low"], r["close"], r.get("tick_volume", 0)) for r in rates], dtype=dtype)
        except Exception as e:
            print(f"    Attempt {attempt+1} failed: {e}")
            if attempt == 2:
                return None
    return None


def fetch_data(symbol, strategy, days, mt5_api_url):
    from datetime import timedelta
    import math

    need_m1 = strategy.requires_m1
    need_m5 = strategy.requires_m5
    need_m15 = strategy.requires_m15
    need_h1 = strategy.requires_h1

    bars_per_day = {
        "m1": 1440,
        "m5": 288,
        "m15": 96,
        "h1": 24,
    }

    dfs = {}
    tf_map = {
        "m1": need_m1,
        "m5": need_m5,
        "m15": need_m15,
        "h1": need_h1,
    }

    now = datetime.now()
    date_to = now
    date_from = now - timedelta(days=days)

    for tf_key, needed in tf_map.items():
        if not needed:
            continue

        bpd = bars_per_day[tf_key]
        total_bars = days * bpd

        if total_bars <= MAX_BARS:
            print(f"  Fetching {tf_key.upper()} ({date_from.date()} -> {date_to.date()})...")
            raw = fetch_rates_range(symbol, tf_key, date_from, date_to, mt5_api_url)
            if raw is None or len(raw) == 0:
                raise ValueError(f"No {tf_key.upper()} data for {symbol}")
            all_raw = [raw]
        else:
            chunk_days = math.ceil(MAX_BARS / bpd * 0.9)
            n_chunks = math.ceil(days / chunk_days)
            print(f"  Fetching {tf_key.upper()} in {n_chunks} chunks ({days} days, ~{total_bars} bars)...")
            all_raw = []
            cursor_from = date_from
            for ci in range(n_chunks):
                cursor_to = min(cursor_from + timedelta(days=chunk_days), date_to)
                print(f"    Chunk {ci+1}/{n_chunks}: {cursor_from.date()} -> {cursor_to.date()}")
                raw = fetch_rates_range(symbol, tf_key, cursor_from, cursor_to, mt5_api_url)
                if raw is not None and len(raw) > 0:
                    all_raw.append(raw)
                else:
                    print(f"    Chunk {ci+1} returned no data, skipping.")
                cursor_from = cursor_to + timedelta(days=1)
                if cursor_from >= date_to:
                    break

        combined = np.concatenate(all_raw) if all_raw else None
        if combined is None or len(combined) == 0:
            raise ValueError(f"No {tf_key.upper()} data for {symbol}")

        df = pd.DataFrame(combined)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
        print(f"  {tf_key.upper()}: {len(df)} bars ({df['time'].iloc[0]} -> {df['time'].iloc[-1]})")
        dfs[tf_key] = df

    return dfs


def run_backtest(strategy, dfs, symbol, lot, balance, cancel_flag=None, progress_callback=None, direction_filter="both"):
    trades = []
    position = None

    df_m1 = dfs.get("m1")
    df_m5 = dfs.get("m5")
    df_m15 = dfs.get("m15")
    df_h1 = dfs.get("h1")

    use_m1 = df_m1 is not None
    use_m5 = df_m5 is not None
    use_m15 = df_m15 is not None
    use_h1 = df_h1 is not None

    if use_m1:
        total = len(df_m1)
        m1_times = df_m1["time"].values

    if use_m5:
        m5_times = df_m5["time"].values
        m5_end = pd.Series(m5_times) - pd.to_timedelta(pd.DatetimeIndex(m5_times).minute % 5, unit="m")

    if use_m15:
        m15_times = df_m15["time"].values
        m15_end = pd.Series(m15_times) - pd.to_timedelta(pd.DatetimeIndex(m15_times).minute % 15, unit="m")

    if use_h1:
        h1_times = df_h1["time"].values
        h1_end = pd.Series(h1_times) - pd.to_timedelta(pd.DatetimeIndex(h1_times).minute % 60, unit="m")

    instr = INSTRUMENT.get(symbol, {"pip": 0.01, "contract": 100})
    pip = instr["pip"]
    contract = instr["contract"]

    print("Pre-calcolo indicatori...")
    if use_m1:
        df_m1["ema9"] = compute_ema(df_m1, 9)
        df_m1["ema21"] = compute_ema(df_m1, 21)
        df_m1["rsi14"] = compute_rsi(df_m1, 14)
        macd, macd_sig = compute_macd(df_m1)
        df_m1["macd"] = macd
        df_m1["macd_sig"] = macd_sig
        atr_s = compute_atr(df_m1)
        df_m1["atr"] = atr_s
        df_m1["atr_ma10"] = atr_s.rolling(10).mean()
        df_m1["candle_body"] = (df_m1["close"] - df_m1["open"]).abs()
        df_m1["is_spike"] = df_m1["candle_body"] > (df_m1["atr"] * 3)

    if use_m5:
        hma = compute_hma(df_m5)
        df_m5["hma"] = hma
        df_m5["hma_prev"] = hma.shift(1)
        df_m5["prev_close"] = df_m5["close"].shift(1)
        df_m5["tr"] = df_m5.apply(lambda r: max(
            r["high"] - r["low"],
            abs(r["high"] - r["prev_close"]),
            abs(r["low"] - r["prev_close"])
        ), axis=1)
        df_m5["atr_m5"] = df_m5["tr"].rolling(14).mean()
        df_m5["ema5"] = compute_ema(df_m5, 5)
        df_m5["ema15"] = compute_ema(df_m5, 15)
        df_m5["ema20"] = compute_ema(df_m5, 20)
        df_m5["rsi14"] = compute_rsi(df_m5, 14)
        df_m5["vol_avg"] = df_m5["tick_volume"].rolling(20).mean()

    if use_m15:
        df_m15["ema50"] = compute_ema(df_m15, 50)
        df_m15["ema200"] = compute_ema(df_m15, 200)
        df_m15["ema5"] = compute_ema(df_m15, 5)
        df_m15["ema20_m15"] = compute_ema(df_m15, 20)
        hma15 = compute_hma(df_m15)
        df_m15["hma"] = hma15
        df_m15["hma_prev"] = hma15.shift(1)
        df_m15["rsi14"] = compute_rsi(df_m15, 14)
        df_m15["vol_avg"] = df_m15["tick_volume"].rolling(20).mean()

    if use_h1:
        tenkan, kijun, senkou_a, senkou_b, chikou = compute_ichimoku(df_h1)
        df_h1["tenkan"] = tenkan
        df_h1["kijun"] = kijun
        df_h1["senkou_a"] = senkou_a
        df_h1["senkou_b"] = senkou_b
        df_h1["chikou"] = chikou

    if use_h1:
        hma_h1 = compute_hma(df_h1)
        df_h1["hma"] = hma_h1
        df_h1["hma_prev"] = hma_h1.shift(1)
        df_h1["rsi14"] = compute_rsi(df_h1, 14)
        df_h1["prev_close"] = df_h1["close"].shift(1)
        df_h1["tr"] = df_h1.apply(lambda r: max(
            r["high"] - r["low"],
            abs(r["high"] - r["prev_close"]) if pd.notna(r["prev_close"]) else r["high"] - r["low"],
            abs(r["low"] - r["prev_close"]) if pd.notna(r["prev_close"]) else r["high"] - r["low"]
        ), axis=1)
        df_h1["atr_h1"] = df_h1["tr"].rolling(14).mean()
        df_h1["vol_avg"] = df_h1["tick_volume"].rolling(20).mean()

    print("Esecuzione backtest...")
    if use_m1:
        primary_df = df_m1
        primary_times = m1_times
        lookback = M1_LOOKBACK
        entry_step = 1
    elif use_m15:
        primary_df = df_m15
        primary_times = m15_times
        lookback = 70
        entry_step = 1
    elif use_h1:
        primary_df = df_h1
        primary_times = h1_times
        lookback = 78
        entry_step = 1
    else:
        print("Nessun timeframe primario disponibile")
        return trades, balance

    total = len(primary_df)
    start_idx = lookback
    print_steps = max(total // 20, 1)

    for i in range(start_idx, total):
        if cancel_flag and cancel_flag():
            break

        ts = primary_times[i]
        row = primary_df.iloc[i]
        low = row["low"]
        high = row["high"]
        price = row["close"]
        ts_str = str(ts)

        if i % print_steps == 0:
            pct = (i - start_idx) / max(total - start_idx, 1) * 100
            print(f"  ... {pct:.0f}% ({i}/{total}) | Trades: {len(trades)} | Bal: {balance:.2f}")
            if progress_callback:
                progress_callback(round(pct), len(trades), round(balance, 2))

        if position:
            entry, direction, sl, tp = position

            if direction == "buy":
                if low <= sl:
                    pnl = (sl - entry) * lot * contract
                    balance += pnl
                    print(f"{ts_str} BUY SL @ {sl:.2f} | PnL: {pnl:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "BUY", "exit": "SL", "pnl": pnl, "balance": balance})
                    position = None
                elif high >= tp:
                    gain = (tp - entry) * lot * contract
                    balance += gain
                    print(f"{ts_str} BUY TP @ {tp:.2f} | PnL: {gain:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "BUY", "exit": "TP", "pnl": gain, "balance": balance})
                    position = None
            else:
                if high >= sl:
                    pnl = (entry - sl) * lot * contract
                    balance += pnl
                    print(f"{ts_str} SELL SL @ {sl:.2f} | PnL: {pnl:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "SELL", "exit": "SL", "pnl": pnl, "balance": balance})
                    position = None
                elif low <= tp:
                    gain = (entry - tp) * lot * contract
                    balance += gain
                    print(f"{ts_str} SELL TP @ {tp:.2f} | PnL: {gain:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "SELL", "exit": "TP", "pnl": gain, "balance": balance})
                    position = None

        if position is None and i % entry_step == 0:
            ind = Indicators()
            ind.session_label = _get_session_label(ts)

            if use_m1:
                ind.ema_fast = row["ema9"]
                ind.ema_slow = row["ema21"]
                ind.rsi_m1 = row["rsi14"]
                ind.macd = row["macd"]
                ind.macd_sig = row["macd_sig"]
                ind.volatilty_expansion = row["atr"] > row["atr_ma10"]
                ind.volatility_expansion = ind.volatilty_expansion
                ind.is_spike = row["is_spike"]
                ind.atr_m1 = row["atr"]

                if use_m5:
                    idx_m5 = int(m5_end.searchsorted(ts, side="right")) - 1
                    if idx_m5 >= 30:
                        ind.hma_m5 = df_m5.iloc[idx_m5]["hma"]
                        ind.hma_m5_prev = df_m5.iloc[idx_m5]["hma_prev"]
                        ind.atr_m5_val = df_m5.iloc[idx_m5]["atr_m5"]
                        ind.ema_short = df_m5.iloc[idx_m5]["ema5"]
                        ind.ema_long = df_m5.iloc[idx_m5]["ema15"]
                        ind.rsi = df_m5.iloc[idx_m5]["rsi14"]

                if use_m15:
                    idx_m15 = int(m15_end.searchsorted(ts, side="right")) - 1
                    if idx_m15 >= 30:
                        price_m15 = df_m15.iloc[idx_m15]["close"]
                        ind.trend_macro_up = price_m15 > df_m15.iloc[idx_m15]["ema50"]
                        ind.trend_macro_50_up = price_m15 > df_m15.iloc[idx_m15]["ema50"]
                        ind.ema_short = getattr(ind, "ema_short", None) or df_m15.iloc[idx_m15]["ema5"]
                        ind.ema_long = getattr(ind, "ema_long", None) or df_m15.iloc[idx_m15]["ema20_m15"]
                        ind.hma = df_m15.iloc[idx_m15]["hma"]
                        ind.hma_prev = df_m15.iloc[idx_m15]["hma_prev"]
                        vol_now = df_m15.iloc[idx_m15]["tick_volume"]
                        vol_avg = df_m15.iloc[idx_m15]["vol_avg"]
                        ind.volume_ok = vol_now > vol_avg * 1.2 if pd.notna(vol_avg) and vol_avg > 0 else True

                if use_h1:
                    idx_h1 = int(h1_end.searchsorted(ts, side="right")) - 1
                    if idx_h1 >= 60:
                        ind.tenkan = df_h1.iloc[idx_h1]["tenkan"]
                        ind.kijun = df_h1.iloc[idx_h1]["kijun"]
                        ind.senkou_a = df_h1.iloc[idx_h1]["senkou_a"]
                        ind.senkou_b = df_h1.iloc[idx_h1]["senkou_b"]
                        ind.chikou = df_h1.iloc[idx_h1]["chikou"]
                        ind.price = df_h1.iloc[idx_h1]["close"]
            elif use_h1:
                ind.tenkan = row.get("tenkan")
                ind.kijun = row.get("kijun")
                ind.senkou_a = row.get("senkou_a")
                ind.senkou_b = row.get("senkou_b")
                ind.chikou = row.get("chikou")
                ind.price = price
                ind.rsi = row.get("rsi14")
                ind.hma = row.get("hma")
                ind.hma_prev = row.get("hma_prev")
            elif use_m15:
                ind.ema_short = row.get("ema5")
                ind.ema_long = row.get("ema20_m15")
                ind.rsi = row.get("rsi14")
                ind.hma = row.get("hma")
                ind.hma_prev = row.get("hma_prev")
                vol_now = row.get("tick_volume", 0)
                vol_avg = row.get("vol_avg")
                ind.volume_ok = vol_now > vol_avg * 1.2 if pd.notna(vol_avg) and vol_avg > 0 else True
                ind.trend_macro_up = price > row.get("ema50", price) if pd.notna(row.get("ema50")) else None
                ind.trend_macro_50_up = ind.trend_macro_up

            try:
                buy = strategy.buy_condition(ind)
                sell = strategy.sell_condition(ind)
            except Exception:
                continue

            direction = None
            if buy and direction_filter in ("buy", "both"):
                direction = "buy"
            elif sell and direction_filter in ("sell", "both"):
                direction = "sell"

            if direction:
                sl_pts, tp_pts = strategy.get_dynamic_sl_tp(ind)
                if sl_pts is None:
                    sl_pts = DEFAULT_SL
                if tp_pts is None:
                    tp_pts = DEFAULT_TP

                sl_price = price - (sl_pts * pip) if direction == "buy" else price + (sl_pts * pip)
                tp_price = price + (tp_pts * pip) if direction == "buy" else price - (tp_pts * pip)
                position = (price, direction, sl_price, tp_price)
                log = strategy.get_log_details(ind) if hasattr(strategy, "get_log_details") else ""
                print(f"{ts_str} ENTRY {direction.upper()} @ {price:.2f} SL={sl_price:.2f} TP={tp_price:.2f} | {log}")

    print(f"\nFinal balance: {balance:.2f}")
    return trades, balance


def summary(trades, balance, initial_balance):
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]

    print("\n" + "=" * 60)
    print("BACKTEST SUMMARY")
    print("=" * 60)
    print(f"Total trades     : {len(trades)}")
    print(f"Wins             : {len(wins)}")
    print(f"Losses           : {len(losses)}")
    print(f"Win rate         : {len(wins)/len(trades)*100:.1f}%" if trades else "Win rate: N/A")
    print(f"Gross profit     : {sum(t['pnl'] for t in wins):.2f}")
    print(f"Gross loss       : {sum(t['pnl'] for t in losses):.2f}")
    print(f"Net PnL          : {sum(t['pnl'] for t in trades):.2f}")
    print(f"Final balance    : {balance:.2f}")
    print(f"Return           : {(balance - initial_balance) / initial_balance * 100:.1f}%")

    if wins:
        print(f"Avg win          : {sum(t['pnl'] for t in wins)/len(wins):.2f}")
    if losses:
        print(f"Avg loss         : {sum(t['pnl'] for t in losses)/len(losses):.2f}")
    if wins and losses:
        wr = abs(sum(t['pnl'] for t in wins) / len(wins)) / abs(sum(t['pnl'] for t in losses) / len(losses))
        print(f"Win/Loss ratio   : {wr:.2f}")

    max_dd = 0
    peak = initial_balance
    for t in trades:
        if t["balance"] > peak:
            peak = t["balance"]
        dd = peak - t["balance"]
        if dd > max_dd:
            max_dd = dd
    print(f"Max drawdown     : {max_dd:.2f}")
    print("=" * 60)

    for t in trades[-10:]:
        print(f"{t['time']} | {t['type']:5} | {t['exit']:10} | PnL: {t['pnl']:8.2f} | Bal: {t['balance']:.2f}")


def compute_summary(trades, balance, initial_balance, days=None):
    from zoneinfo import ZoneInfo

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]

    max_dd = 0
    peak = initial_balance
    for t in trades:
        if t["balance"] > peak:
            peak = t["balance"]
        dd = peak - t["balance"]
        if dd > max_dd:
            max_dd = dd

    win_rate = len(wins) / len(trades) * 100 if trades else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = sum(t["pnl"] for t in losses)
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    win_loss_ratio = abs(avg_win / avg_loss) if losses and wins else 0

    def _get_session(dt):
        h, m = dt.hour, dt.minute
        if 1 <= h < 9:
            return "ASIA"
        elif 9 <= h < 14:
            return "LONDON"
        elif 14 <= h < 17 or (h == 17 and m < 30):
            return "NY-LON"
        elif (h == 17 and m >= 30) or 18 <= h < 22:
            return "NY"
        else:
            return "OFF"

    session_stats = {}
    hour_stats = {}
    dow_stats = {}
    direction_stats = {"BUY": {"wins": 0, "losses": 0, "pnl": 0}, "SELL": {"wins": 0, "losses": 0, "pnl": 0}}

    for t in trades:
        try:
            dt = pd.Timestamp(t["time"]).to_pydatetime().replace(tzinfo=ZoneInfo("Europe/Rome"))
        except Exception:
            continue

        session = _get_session(dt)
        hour = dt.hour
        dow = dt.strftime("%A")
        is_win = t["pnl"] > 0

        if session not in session_stats:
            session_stats[session] = {"wins": 0, "losses": 0, "pnl": 0}
        session_stats[session]["wins"] += 1 if is_win else 0
        session_stats[session]["losses"] += 1 if not is_win else 0
        session_stats[session]["pnl"] = round(session_stats[session]["pnl"] + t["pnl"], 2)

        if hour not in hour_stats:
            hour_stats[hour] = {"wins": 0, "losses": 0, "pnl": 0}
        hour_stats[hour]["wins"] += 1 if is_win else 0
        hour_stats[hour]["losses"] += 1 if not is_win else 0
        hour_stats[hour]["pnl"] = round(hour_stats[hour]["pnl"] + t["pnl"], 2)

        if dow not in dow_stats:
            dow_stats[dow] = {"wins": 0, "losses": 0, "pnl": 0}
        dow_stats[dow]["wins"] += 1 if is_win else 0
        dow_stats[dow]["losses"] += 1 if not is_win else 0
        dow_stats[dow]["pnl"] = round(dow_stats[dow]["pnl"] + t["pnl"], 2)

        direction = t["type"]
        direction_stats[direction]["wins"] += 1 if is_win else 0
        direction_stats[direction]["losses"] += 1 if not is_win else 0
        direction_stats[direction]["pnl"] = round(direction_stats[direction]["pnl"] + t["pnl"], 2)

    for d in [*session_stats.values(), *hour_stats.values(), *dow_stats.values(), *direction_stats.values()]:
        total = d["wins"] + d["losses"]
        d["win_rate"] = round(d["wins"] / total * 100, 1) if total > 0 else 0
        d["total"] = total
        d["avg_pnl"] = round(d["pnl"] / total, 2) if total > 0 else 0

    hour_sorted = dict(sorted(hour_stats.items()))

    days_span = days or 1
    if not days and len(trades) >= 2:
        try:
            first = pd.Timestamp(trades[0]["time"])
            last = pd.Timestamp(trades[-1]["time"])
            days_span = max((last - first).days, 1)
        except Exception:
            pass
    trades_per_day = round(len(trades) / days_span, 1) if trades else 0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "trades_per_day": trades_per_day,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(gross_profit + gross_loss, 2),
        "final_balance": round(balance, 2),
        "return_pct": round((balance - initial_balance) / initial_balance * 100, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_loss_ratio": round(win_loss_ratio, 2),
        "max_drawdown": round(max_dd, 2),
        "by_session": session_stats,
        "by_hour": hour_sorted,
        "by_day": dow_stats,
        "by_direction": direction_stats,
    }


def run_backtest_api(strategy_name, symbol, days, lot, balance, mt5_api_url, cancel_flag=None, progress_callback=None, direction="both"):
    strategy = STRATEGIES.get(strategy_name)
    if not strategy:
        return {"error": f"Unknown strategy: {strategy_name}"}

    try:
        dfs = fetch_data(symbol, strategy, days, mt5_api_url)
        trades, final_bal = run_backtest(strategy, dfs, symbol, lot, balance, cancel_flag=cancel_flag, progress_callback=progress_callback, direction_filter=direction)
        summary_data = compute_summary(trades, final_bal, balance, days)

        serializable_trades = []
        for t in trades:
            serializable_trades.append({
                "time": str(t["time"]),
                "type": t["type"],
                "exit": t["exit"],
                "pnl": round(t["pnl"], 2),
                "balance": round(t["balance"], 2),
            })

        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "days": days,
            "lot": lot,
            "initial_balance": balance,
            "summary": summary_data,
            "trades": serializable_trades,
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MT5 Pulse Backtest")
    parser.add_argument("--strategy", "-s", default="SUPER", choices=list(STRATEGIES.keys()), help="Strategy name")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (default: XAUUSD)")
    parser.add_argument("--days", "-d", type=int, default=DEFAULT_DAYS, help="Days of history (default: 30)")
    parser.add_argument("--lot", "-l", type=float, default=DEFAULT_LOT, help="Lot size (default: 0.01)")
    parser.add_argument("--balance", "-b", type=float, default=DEFAULT_BALANCE, help="Starting balance (default: 10000)")
    parser.add_argument("--mt5-api-url", required=True, help="URL del mt5_api (es. http://192.168.1.180:8000)")
    args = parser.parse_args()

    strategy = STRATEGIES[args.strategy]
    print(f"Backtest: {args.strategy} | {args.symbol} | {args.days} days | {args.lot} lot | ${args.balance}")
    print(f"MT5 API: {args.mt5_api_url}")

    print("Fetching data via mt5_api...")
    dfs = fetch_data(args.symbol, strategy, args.days, args.mt5_api_url)
    print(f"Running backtest...")
    trades, final_bal = run_backtest(strategy, dfs, args.symbol, args.lot, args.balance)
    summary(trades, final_bal, args.balance)
