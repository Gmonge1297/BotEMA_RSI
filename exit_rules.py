def check_exit_signal(df, entry_price, direction):
    """
    Detecta si hay señal de reversa.
    Requiere que al menos 2 condiciones se cumplan.
    df = dataframe con velas recientes
    entry_price = precio de entrada del trade
    direction = "BUY" o "SELL"
    """

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    conditions = 0

    # 1. EMA20 plana por 3 velas
    ema20 = df["EMA20"]
    if abs(ema20.iloc[-1] - ema20.iloc[-2]) < 0.10 and abs(ema20.iloc[-2] - ema20.iloc[-3]) < 0.10:
        conditions += 1

    # 2. RSI cae por debajo de 48 si es BUY (o sube de 52 si es SELL)
    if direction == "BUY" and latest["RSI"] < 48:
        conditions += 1
    if direction == "SELL" and latest["RSI"] > 52:
        conditions += 1

    # 3. Dos velas cierran debajo/encima de EMA20
    if direction == "BUY":
        if df["Close"].iloc[-1] < ema20.iloc[-1] and df["Close"].iloc[-2] < ema20.iloc[-2]:
            conditions += 1
    else:  # SELL
        if df["Close"].iloc[-1] > ema20.iloc[-1] and df["Close"].iloc[-2] > ema20.iloc[-2]:
            conditions += 1

    # 4. Vela fuerte contra tendencia rompe últimos mínimos/máximos
    if direction == "BUY":
        if latest["Low"] < min(df["Low"].iloc[-4:-1]):
            conditions += 1
    else:
        if latest["High"] > max(df["High"].iloc[-4:-1]):
            conditions += 1

    # Si no hay al menos 2 condiciones, no hay salida
    if conditions < 2:
        return False

    # Finalmente: ¿estamos en profit?
    current_price = latest["Close"]

    if direction == "BUY" and current_price > entry_price:
        return True

    if direction == "SELL" and current_price < entry_price:
        return True

    return False
