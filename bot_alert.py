import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz

# ==========================
# CONFIGURACIONES DEL BOT
# ==========================

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

INTERVAL = "1h"     # Timeframe 1 hora
PERIOD = "7d"       # 7 días de historial para evitar errores

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

# ==========================
# FUNCIONES DEL BOT
# ==========================

def log(msg):
    cr = datetime.now(pytz.timezone("America/Costa_Rica"))
    print(f"[ {cr} ] {msg}")

def calculate_indicators(df):
    df["EMA20"] = df["Close"].rolling(EMA_FAST).mean()
    df["EMA50"] = df["Close"].rolling(EMA_SLOW).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

def check_signals(df):
    last = df.iloc[-1]

    ema20 = last["EMA20"]
    ema50 = last["EMA50"]
    rsi = last["RSI"]
    price = last["Close"]

    # Señal de compra
    if ema20 > ema50 and rsi > 50:
        return f"🟢 BUY | Precio: {price:.5f} | EMA20>EMA50 | RSI {rsi:.1f}"

    # Señal de venta
    if ema20 < ema50 and rsi < 50:
        return f"🔴 SELL | Precio: {price:.5f} | EMA20<EMA50 | RSI {rsi:.1f}"

    return None

def process_symbol(name, yf_symbol):
    log(f"Descargando datos de {name} ({yf_symbol})...")

    try:
        df = yf.download(yf_symbol, interval=INTERVAL, period=PERIOD, progress=False)
    except Exception as e:
        print(f"❌ Error descargando {name}: {e}\n")
        return

    # Validación mejorada
    if df is None or df.empty or len(df) < 60:
        print("⚠️ No hay suficientes velas.\n")
        return

    df = calculate_indicators(df)

    signal = check_signals(df)

    if signal:
        print(f"✅ Señal encontrada en {name}: {signal}\n")
    else:
        print("ℹ️ Sin señal.\n")


# ==========================
# EJECUCIÓN PRINCIPAL
# ==========================

if __name__ == "__main__":
    print("\n=== Bot EMA+RSI Agresivo ===\n")

    for name, symbol in SYMBOLS.items():
        process_symbol(name, symbol)

    print("=== Fin ejecución ===\n")
