# STRATEGIA SUPER — XAUUSD

## Come funziona

Ogni 60 secondi (polling) la strategia guarda le candele M1, M5 e M15 e decide se aprire un trade.

## Segnale BUY

Tutte le condizioni devono essere true contemporaneamente:

| # | Condizione | Timeframe | Significato |
|---|-----------|-----------|-------------|
| 1 | EMA9 > EMA21 | M1 | Trend up a breve |
| 2 | MACD > MACD Signal | M1 | Momentum in salita |
| 3 | HMA > HMA precedente | M5 | Trend up a medio |
| 4 | Prezzo > EMA50 | M15 | Trend macro in salita |
| 5 | RSI tra 45 e 70 | M1 | Non ipervenduto, non ipercomprato |
| 6 | ATR > media ATR(10) | M1 | Volatilità in espansione |
| 7 | Corpo candela < 3×ATR | M1 | Non è uno spike |

## Segnale SELL

Inverso di BUY:

| # | Condizione | Timeframe | Significato |
|---|-----------|-----------|-------------|
| 1 | EMA9 < EMA21 | M1 | Trend down a breve |
| 2 | MACD < MACD Signal | M1 | Momentum in discesa |
| 3 | HMA < HMA precedente | M5 | Trend down a medio |
| 4 | Prezzo < EMA50 | M15 | Trend macro in discesa |
| 5 | RSI tra 30 e 55 | M1 | Non ipervenduto, non ipercomprato |
| 6 | ATR > media ATR(10) | M1 | Volatilità in espansione |
| 7 | Corpo candela < 3×ATR | M1 | Non è uno spike |

## Segnale HOLD

Nessuna delle due → non fa niente. Non chiude posizioni.

## SL / TP

| Condizione | SL (punti) | TP (punti) |
|------------|-----------|-----------|
| ATR M5 ≤ 12 | 1000 | 1500 |
| ATR M5 > 12 | 500 (default) | 600 (default) |

## Gestione posizioni

- **Max posizioni aperte**: 1 per direzione (hedging: BUY e SELL possono coesistere)
- **Non ribalta** mai le posizioni (reverse = False)
- **Non chiude** su HOLD — asetta solo SL/TP del broker
- Se hai un BUY aperto e arriva un SELL signal → apre anche il SELL (hedging)
- Se hai un BUY aperto e arriva un altro BUY signal → non fa niente (BUY già aperto)

## Timeframe usati

| Timeframe | Cosa fa |
|-----------|---------|
| M1 | EMA9/21, MACD, RSI, ATR, spike detection |
| M5 | HMA trend, ATR per SL/TP dinamico |
| M15 | EMA50 per trend macro |

## Note importanti

- La candela analizzata è la **penultima** chiusa (non quella corrente)
- Il broker gestisce SL/TP in tempo reale (tick by tick)
- Il backtest controlla SL/TP solo alla chiusura di ogni candela → può differire dal live
