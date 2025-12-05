import yfinance as yf
import pandas as pd
from datetime import datetime
import numpy as np

# ================================
# CONFIGURACIONES
# ================================
PARES = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

MIN_CANDLES = 50  # para evitar errores de EMA

# ================================
# FUNCIÓN: DESCARGAR DATOS
# ================================
def descargar_datos(ticker):
    try:
        df = yf.download(
            ticker, 
            interval="30m",
            period="5d",
            progress=False
        )

        if df is None or df.empty:
            return None, "Datos vacíos"

        if len(df) < MIN_CANDLES:
            return None, "Datos insuficientes"

        df = df[['Open','High','Low','Close','Volume']].copy()
        df.dropna(inplace=True)

        return df, None

    except Exception as e:
        return None, str(e)

# ================================
# FUNCIÓN: AGREGAR INDICADORES
# ================================
def agregar_indicadores(df):
    df = df.copy()

    # ----- EMAs -----
    df["ema_fast"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["ema_slow"] = df["Close"].ewm(span=21, adjust=False).mean()

    # ----- RSI -----
    delta = df["Close"].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    rs = up.ewm(span=14, adjust=False).mean() / down.ewm(span=14, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + rs))

    df.dropna(inplace=True)
    return df

# ================================
# FUNCIÓN: GENERAR SEÑALES
# ================================
def generar_senal(df):
    c1 = df["ema_fast"].iloc[-1]
    c2 = df["ema_slow"].iloc[-1]
    rsi = df["rsi"].iloc[-1]
    close = df["Close"].iloc[-1]

    # Señal BUY
    if c1 > c2 and rsi > 50:
        sl = round(close * 0.996, 5)
        tp = round(close * 1.004, 5)
        return "BUY", close, sl, tp

    # Señal SELL
    if c1 < c2 and rsi < 50:
        sl = round(close * 1.004, 5)
        tp = round(close * 0.996, 5)
        return "SELL", close, sl, tp

    return None, None, None, None

# ================================
# PROCESO PRINCIPAL
# ================================
def ejecutar_bot():
    print(f"[{datetime.utcnow()}] === Bot PRO ejecutándose (modo CRON) ===\n")

    for nombre, ticker in PARES.items():
        print(f"[{datetime.utcnow()}] Analizando {nombre} ({ticker})\n")

        df, err = descargar_datos(ticker)
        if err:
            print(f"[ERROR descarga]: {err}\n")
            print("  — Datos insuficientes.\n")
            continue

        df = agregar_indicadores(df)

        senal, entry, sl, tp = generar_senal(df)
        if senal:
            print(f"SEÑAL {nombre}: {senal}")
            print(f"  Entrada: {entry}")
            print(f"  SL: {sl}")
            print(f"  TP: {tp}\n")
        else:
            print("Sin señal válida.\n")

    print(f"[{datetime.utcnow()}] === Fin del ciclo PRO ===\n")
    print(f"Bot finalizado - {datetime.utcnow()}\n")

# ================================
# EJECUCIÓN
# ================================
if __name__ == "__main__":
    ejecutar_bot()
