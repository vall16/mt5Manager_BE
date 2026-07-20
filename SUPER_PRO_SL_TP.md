# SUPER PRO - SL/TP per Sessione

SL/TP dinamici basati su **ATR M5** e **sessione di trading** (Europe/Rome).

```
SL = ATR_M5 × factor_SL × 10  (min 300, max 2000 punti)
TP = ATR_M5 × factor_TP × 10  (min SL+100, max 3000 punti)
```

1 punto = $0.01 su XAUUSD (es. 750 punti = $7.50)

| Sessione | Ore (Roma)   | SL factor | TP factor | RSI period | RSI buy range   | RSI sell range  |
|----------|-------------|-----------|-----------|------------|-----------------|-----------------|
| ASIA     | 01:00-09:00 | 4.0       | 2.0       | 21         | 50-78           | 22-50          
| LONDON   | 09:00-14:00 | 3.0       | 1.8       | 14         | 45-72           | 28-55           |
| NY-LON   | 14:00-17:30 | 2.5       | 2.2       | 9          | 42-68           | 32-58           |
| NY       | 17:30-22:00 | 3.0       | 1.5       | 14         | 45-70           | 30-55           |
| OFF      | 22:00-01:00 | 5.0       | 1.2       | 21         | 55-85           | 15-45           |

## Esempio pratico

ATR_M5 = 25 su XAUUSD durante LONDON:
- SL = 25 × 3.0 × 10 = 750 punti = $7.50
- TP = 25 × 1.8 × 10 = 450 punti = $4.50

ATR_M5 = 30 su XAUUSD durante NY-LON:
- SL = 30 × 2.5 × 10 = 750 punti = $7.50
- TP = 30 × 2.2 × 10 = 660 punti = $6.60

## Condizioni di entrata

**BUY**: trend_macro_up + trend_macro_50_up + ema_fast > ema_slow + macd > macd_sig + hma_m5 > hma_m5_prev + RSI nel range

**SELL**: !trend_macro_up + !trend_macro_50_up + ema_fast < ema_slow + macd < macd_sig + hma_m5 < hma_m5_prev + RSI nel range

## Exit in hold

Chiudi buy se trend macro inverte + hma_m5 scende.
Chiudi sell se trend macro sale + hma_m5 sale.
