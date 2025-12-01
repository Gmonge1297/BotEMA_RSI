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
PERIOD = "7d"       # 7 días de historial

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

# ==========================
# FUNCIONES
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

    ema20 = float(last["EMA20"])
    ema50 = float(last["EMA50"])
    rsi = float(last["RSI"])
    price = float(last["Close"])

    if ema20 > ema50 and rsi > 50:
        return f"🟢 BUY | Precio: {price:.5f} | EMA20>EMA50 | RSI {rsi:.1f}"

    if ema20 < ema50 and rsi < 50:
        return f"🔴 SELL | Precio: {price:.5f} | EMA20<EMA50 | RSI {rsi:.1f}"

    return None


# ==========================
# PROCESAR CADA PAR
# ==========================

def process_symbol(name, yf_symbol):
    log(f"Descargando datos de {name} ({yf_symbol})...")

    try:
        df = yf.download(
            yf_symbol,
            interval=INTERVAL,
            period=PERIOD,
            progress=False,
            auto_adjust=True
        )
    except Exception as e:
        print(f"❌ Error descargando {name}: {e}\n")
        return

    if df is None or df.empty:
        print("⚠️ No hay datos descargados.\n")
        return

    # Normalizar columnas (a veces yfinance devuelve MultiIndex)
    cols = []
    for c in df.columns:
        if isinstance(c, tuple):
            nc = next((x for x in c if x), c[0])
        else:
            nc = c
        if nc == 'Adj Close':
            nc = 'Close'
        cols.append(nc)

    df.columns = cols

    if "Close" not in df.columns:
        print("⚠️ La columna 'Close' no existe. Columnas recibidas:", df.columns.tolist(), "\n")
        return

    df.dropna(inplace=True)

    if len(df) < 60:
        print("⚠️ No hay suficientes velas (mínimo 60).\n")
        return

    df = calculate_indicators(df)

    # DEBUG → valores escalares para evitar errores de formato
    try:
        close_last = float(df["Close"].iat[-1])
        ema20_last = float(df["EMA20"].iat[-1])
        ema50_last = float(df["EMA50"].iat[-1])
        rsi_last = float(df["RSI"].iat[-1])

        print(f"DEBUG {name} → Close:{close_last:.5f}, EMA20:{ema20_last:.5f}, EMA50:{ema50_last:.5f}, RSI:{rsi_last:.2f}")
    except Exception as e:
        print(f"⚠️ Error obteniendo valores escalares en {name}: {e}")
        print("Columnas:", df.columns.tolist())
        print("Última fila:")
        print(df.tail(1))
        pass

    try:
        signal = check_signals(df)
    except Exception as e:
        print(f"❌ Error evaluando señal en {name}: {e}\n")
        signal = None

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
