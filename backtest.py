import MetaTrader5 as mt5
import pandas as pd
import argparse
import sys
from datetime import datetime, timedelta

from trading_signals_multi2 import STRATEGIES, Indicators

DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_DAYS = 30
DEFAULT_LOT = 0.01
DEFAULT_BALANCE = 10000.0
M1_LOOKBACK = 100
MAX_BARS = 50000

INSTRUMENT = {
    "XAUUSD":  {"pip": 0.01,    "contract": 100},
    "USDJPY":  {"pip": 0.01,    "contract": 1000},
    "GBPUSD":  {"pip": 0.0001, "contract": 100000},
    "GBPJPY":  {"pip": 0.01,   "contract": 1000},
    "AUDJPY":  {"pip": 0.01,   "contract": 1000},
    "EURUSD":  {"pip": 0.0001, "contract": 100000},
    "MSFT":    {"pip": 0.01,   "contract": 100},
    "NVDA":    {"pip": 0.01,   "contract": 100},
}

DEFAULT_SL = 500
DEFAULT_TP = 600


def fetch_data(symbol, strategy, days):
    need_m1 = strategy.requires_m1
    need_m5 = strategy.requires_m5
    need_m15 = strategy.requires_m15
    need_h1 = strategy.requires_h1

    dfs = {}

    if need_m1:
        print(f"  Fetching M1...")
        raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, MAX_BARS)
        if raw is None or len(raw) == 0:
            raise ValueError(f"No M1 data for {symbol}")
        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        cutoff = datetime.now() - timedelta(days=days)
        df = df[df["time"] >= pd.Timestamp(cutoff)].reset_index(drop=True)
        print(f"  M1: {len(df)} bars ({df['time'].iloc[0]} -> {df['time'].iloc[-1]})")
        dfs["m1"] = df

    if need_m5:
        print(f"  Fetching M5...")
        raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, MAX_BARS)
        if raw is None or len(raw) == 0:
            raise ValueError(f"No M5 data for {symbol}")
        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        dfs["m5"] = df

    if need_m15:
        print(f"  Fetching M15...")
        raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, MAX_BARS)
        if raw is None or len(raw) == 0:
            raise ValueError(f"No M15 data for {symbol}")
        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        dfs["m15"] = df

    if need_h1:
        print(f"  Fetching H1...")
        raw = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, MAX_BARS)
        if raw is None or len(raw) == 0:
            raise ValueError(f"No H1 data for {symbol}")
        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        dfs["h1"] = df

    return dfs


def run_backtest(strategy, dfs, symbol, lot, balance):
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
    start_idx = M1_LOOKBACK if use_m1 else 0
    print_steps = max(total // 20, 1) if use_m1 else 1

    for i in range(start_idx, total if use_m1 else 1):
        if use_m1 and i % print_steps == 0:
            pct = (i - start_idx) / max(total - start_idx, 1) * 100
            print(f"  ... {pct:.0f}% ({i}/{total}) | Trades: {len(trades)} | Bal: {balance:.2f}")

        ts = m1_times[i] if use_m1 else None

        slices = {}
        if use_m1:
            slices["df_m1"] = df_m1.iloc[:i + 1]
        if use_m5:
            idx_m5 = int(m5_end.searchsorted(ts, side="right")) - 1 if ts is not None else len(df_m5) - 1
            if idx_m5 < 30:
                continue
            slices["df_m5"] = df_m5.iloc[:idx_m5 + 1]
        if use_m15:
            idx_m15 = int(m15_end.searchsorted(ts, side="right")) - 1 if ts is not None else len(df_m15) - 1
            if idx_m15 < 30:
                continue
            slices["df_m15"] = df_m15.iloc[:idx_m15 + 1]
        if use_h1:
            idx_h1 = int(h1_end.searchsorted(ts, side="right")) - 1 if ts is not None else len(df_h1) - 1
            if idx_h1 < 60:
                continue
            slices["df_h1"] = df_h1.iloc[:idx_h1 + 1]

        try:
            ind = strategy.compute_indicators(
                slices.get("df_m1"),
                slices.get("df_m5"),
                slices.get("df_m15"),
                slices.get("df_h1"),
            )
        except Exception:
            continue

        if use_m1:
            row = df_m1.iloc[i]
            low = row["low"]
            high = row["high"]
            price = row["close"]
            ts_str = str(ts)
        else:
            continue

        if position:
            entry, direction, sl, tp = position

            if direction == "buy":
                action = strategy.on_hold_action(ind, True, False, "BUY") if hasattr(strategy, "on_hold_action") else None
                if action == "close_buy" or low <= sl:
                    hit_sl = low <= sl
                    exit_price = sl if hit_sl else price
                    pnl = (exit_price - entry) * lot * contract
                    exit_type = "SL" if hit_sl else "HOLD_EXIT"
                    balance += pnl
                    print(f"{ts_str} BUY {exit_type} @ {exit_price:.2f} | PnL: {pnl:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "BUY", "exit": exit_type, "pnl": pnl, "balance": balance})
                    position = None
                elif high >= tp:
                    gain = (tp - entry) * lot * contract
                    balance += gain
                    print(f"{ts_str} BUY TP @ {tp:.2f} | PnL: {gain:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "BUY", "exit": "TP", "pnl": gain, "balance": balance})
                    position = None
            else:
                action = strategy.on_hold_action(ind, False, True, "SELL") if hasattr(strategy, "on_hold_action") else None
                if action == "close_sell" or high >= sl:
                    hit_sl = high >= sl
                    exit_price = sl if hit_sl else price
                    pnl = (entry - exit_price) * lot * contract
                    exit_type = "SL" if hit_sl else "HOLD_EXIT"
                    balance += pnl
                    print(f"{ts_str} SELL {exit_type} @ {exit_price:.2f} | PnL: {pnl:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "SELL", "exit": exit_type, "pnl": pnl, "balance": balance})
                    position = None
                elif low <= tp:
                    gain = (entry - tp) * lot * contract
                    balance += gain
                    print(f"{ts_str} SELL TP @ {tp:.2f} | PnL: {gain:.2f} | Bal: {balance:.2f}")
                    trades.append({"time": ts, "type": "SELL", "exit": "TP", "pnl": gain, "balance": balance})
                    position = None

        if position is None:
            has_sell = False
            has_buy = False

            buy = strategy.buy_condition(ind)
            sell = strategy.sell_condition(ind)

            direction = None
            if buy:
                direction = "buy"
            elif sell:
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MT5 Pulse Backtest")
    parser.add_argument("--strategy", "-s", default="SUPER", choices=list(STRATEGIES.keys()), help="Strategy name")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (default: XAUUSD)")
    parser.add_argument("--days", "-d", type=int, default=DEFAULT_DAYS, help="Days of history (default: 30)")
    parser.add_argument("--lot", "-l", type=float, default=DEFAULT_LOT, help="Lot size (default: 0.01)")
    parser.add_argument("--balance", "-b", type=float, default=DEFAULT_BALANCE, help="Starting balance (default: 10000)")
    args = parser.parse_args()

    strategy = STRATEGIES[args.strategy]
    print(f"Backtest: {args.strategy} | {args.symbol} | {args.days} days | {args.lot} lot | ${args.balance}")

    if not mt5.initialize():
        print("MT5 initialization failed")
        sys.exit(1)

    try:
        print("Fetching data...")
        dfs = fetch_data(args.symbol, strategy, args.days)
        print(f"Running backtest...")
        trades, final_bal = run_backtest(strategy, dfs, args.symbol, args.lot, args.balance)
        summary(trades, final_bal, args.balance)
    finally:
        mt5.shutdown()
