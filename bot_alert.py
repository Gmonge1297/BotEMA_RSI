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
    close = df["Close"]

    # EMA correctas
    df["EMA20"] = close.ewm(span=EMA_FAST, adjust=False).mean()
    df["EMA50"] = close.ewm(span=EMA_SLOW, adjust=False).mean()

    # RSI (EMA smoothing)
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)

    roll_up = up.ewm(span=RSI_PERIOD, adjust=False).mean()
    roll_down = down.ewm(span=RSI_PERIOD, adjust=False).mean()

    rs = roll_up / roll_down
    df["RSI"] = 100 - (100 / (1 + rs))

    df.dropna(inplace=True)
    return df

def check_signals(df):
    last = df.iloc[-1]

    ema20 = float(last["EMA20"])
    ema50 = float(last["EMA50"])
    rsi = float(last["RSI"])
    price = float(last["Close"])

    # Señal de compra
    if ema20 > ema50 and rsi > 50:
        return f"🟢 BUY | {price:.5f} | EMA20>EMA50 | RSI {rsi:.1f}"

    # Señal de venta
    if ema20 < ema50 and rsi < 50:
        return f"🔴 SELL | {price:.5f} | EMA20<EMA50 | RSI {rsi:.1f}"

    return None

def process_symbol(name, yf_symbol):
    log(f"Descargando datos de {name} ({yf_symbol})...")

    try:
        df = yf.download(
            yf_symbol,
            interval=INTERVAL,
            period=PERIOD,
            progress=False,
            auto_adjust=True  # evita warnings
        )
    except Exception as e:
        print(f"❌ Error descargando {name}: {e}\n")
        return

    if df is None or df.empty:
        print("⚠️ No hay datos descargados.\n")
        return

    if "Close" not in df.columns:
        print("⚠️ No existe columna Close.\n")
        return

    df.dropna(inplace=True)
    if len(df) < 60:
        print("⚠️ No hay suficientes velas (mínimo 60).\n")
        return

    df = calculate_indicators(df)

    # DEBUG
    last = df.iloc[-1]
    print(f"DEBUG {name} → Close:{last['Close']:.5f}, EMA20:{last['EMA20']:.5f}, EMA50:{last['EMA50']:.5f}, RSI:{last['RSI']:.2f}")

    # Evaluar señal
    signal = check_signals(df)

    if signal:
        print(f"✅ Señal en {name}: {signal}\n")
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
