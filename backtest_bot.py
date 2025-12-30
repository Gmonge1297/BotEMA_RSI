# Backtest EMA20/50 + RSI
for label, symbol in PARES:
    df = get_h1(symbol, days=10)  # últimos 10 días (~240 velas H1)
    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    rsi_v = rsi(df["close"], 14)

    signals = []
    for i in range(50, len(df)):
        c0, o0 = df["close"].iloc[i-1], df["open"].iloc[i-1]
        buy = (ema20.iloc[i-2] <= ema50.iloc[i-2] and ema20.iloc[i-1] > ema50.iloc[i-1]
               and rsi_v.iloc[i-1] >= 50 and c0 > o0)
        sell = (ema20.iloc[i-2] >= ema50.iloc[i-2] and ema20.iloc[i-1] < ema50.iloc[i-1]
                and rsi_v.iloc[i-1] <= 50 and c0 < o0)
        if buy: signals.append("BUY")
        if sell: signals.append("SELL")

    print(f"{label}: {len(signals)} señales en las últimas {len(df)} velas")
