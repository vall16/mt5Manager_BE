# SUPER_PRO Strategy — XAUUSD

## Overview

Multi-timeframe strategy for XAUUSD using M1/M5/M15 data. Session-adaptive with dynamic SL/TP based on ATR.

**File:** `trading_signals_multi2.py` — class `SuperXauProStrategy` (line 570)

---

## Timeframes Required

| TF   | Use                        |
|------|----------------------------|
| M1   | Entry signals (EMA, RSI, MACD) |
| M5   | HMA trend, ATR for SL/TP  |
| M15  | Macro trend filter (EMA200 + EMA50) |

---

## Session Parameters

### ASIA (01:00 – 09:00)
| Param              | Value |
|--------------------|-------|
| RSI period         | 21    |
| RSI buy range      | 50–78 |
| RSI sell range     | 22–50 |
| Vol expansion mult | 1.3   |
| SL factor          | 4.0   |
| TP factor          | 2.0   |

### LONDON (09:00 – 14:00)
| Param              | Value |
|--------------------|-------|
| RSI period         | 14    |
| RSI buy range      | 45–72 |
| RSI sell range     | 28–55 |
| Vol expansion mult | 1.0   |
| SL factor          | 3.0   |
| TP factor          | 1.8   |

### NY-LON overlap (14:00 – 17:30)
| Param              | Value |
|--------------------|-------|
| RSI period         | 9     |
| RSI buy range      | 42–68 |
| RSI sell range     | 32–58 |
| Vol expansion mult | 0.8   |
| SL factor          | 2.5   |
| TP factor          | 2.2   |

### NY late (17:30 – 22:00)
| Param              | Value |
|--------------------|-------|
| RSI period         | 14    |
| RSI buy range      | 45–70 |
| RSI sell range     | 30–55 |
| Vol expansion mult | 1.0   |
| SL factor          | 3.0   |
| TP factor          | 1.5   |

### OFF (22:00 – 01:00)
| Param              | Value |
|--------------------|-------|
| RSI period         | 21    |
| RSI buy range      | 55–85 |
| RSI sell range     | 15–45 |
| Vol expansion mult | 1.5   |
| SL factor          | 5.0   |
| TP factor          | 1.2   |

---

## Indicators

### M1
- EMA 9 / EMA 21 — trend direction
- RSI (period varies by session) — overbought/oversold filter
- MACD / Signal — momentum confirmation

### M5
- HMA (Hull Moving Average) — trend direction
- ATR 14 — used for dynamic SL/TP calculation

### M15
- EMA 200 — macro trend filter (must align with entry direction)
- EMA 50 — secondary trend confirmation

### Volatility
- ATR M1 rolling 10 mean → compared with `vol_expansion_mult`
- Spike detection: candle body > ATR × 3 → blocks entry

---

## Entry Conditions

### BUY — all must be true:
1. Price > EMA200 M15 (macro trend up)
2. Price > EMA50 M15 (secondary trend up)
3. EMA9 M1 > EMA21 M1 (short-term momentum up)
4. MACD M1 > Signal M1 (momentum confirmed)
5. HMA M5 > HMA M5 prev (M5 trend up)
6. RSI M1 within session buy range
7. Volatility expansion active
8. No spike candle

### SELL — all must be true:
1. Price < EMA200 M15 (macro trend down)
2. Price < EMA50 M15 (secondary trend down)
3. EMA9 M1 < EMA21 M1 (short-term momentum down)
4. MACD M1 < Signal M1 (momentum confirmed)
5. HMA M5 < HMA M5 prev (M5 trend down)
6. RSI M1 within session sell range
7. Volatility expansion active
8. No spike candle

---

## Dynamic SL/TP Calculation

```python
atr = ATR_M5_14  # 14-period ATR on M5

sl = int(atr * sl_atr_factor * 10)
tp = int(atr * tp_atr_factor * 10)

sl = max(300, min(sl, 2000))          # clamp: 300–2000 points
tp = max(sl + 100, min(tp, 3000))     # clamp: SL+100 to 3000 points
```

### Example (LONDON session, ATR M5 = 35 points):
```
sl = 35 × 3.0 × 10 = 1050 → clamped to 1050
tp = 35 × 1.8 × 10 = 630  → clamped to max(1150, 630) = 1150
```

---

## Use Signal SL/TP Toggle

- **ON** (`use_signal_sl_tp = true`): Forces the dynamic ATR-based SL/TP from this strategy, ignoring trader's form values
- **OFF** (`use_signal_sl_tp = false`): Uses the SL/TP set in the trader card form

---

## Differences vs SUPER (SuperXauNoCloseStrategy)

| Feature               | SUPER          | SUPER_PRO              |
|-----------------------|----------------|------------------------|
| M15 trend filter      | EMA 50 only    | EMA 200 + EMA 50       |
| SL/TP                 | Fixed points   | Dynamic ATR-based      |
| Session awareness     | Basic          | Full (5 sessions)      |
| RSI adapts to session | No             | Yes                    |
| Reversal on hold      | Yes            | No                     |

---

## Code Reference

- **Class:** `SuperXauProStrategy` — `trading_signals_multi2.py:570`
- **Session params:** `_get_session_params()` — line 576
- **Indicators:** `compute_indicators()` — line 637
- **Buy logic:** `buy_condition()` — line 695
- **Sell logic:** `sell_condition()` — line 712
- **SL/TP:** `get_dynamic_sl_tp()` — line 735
- **Strategy dict:** `STRATEGIES["SUPER_PRO"]` — line 1300
