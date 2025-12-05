import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import pytz

FOREX_PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

def download_price(symbol):
    """
    Descarga datos y normaliza columnas,
    incluso cuando vienen en MultiIndex.
    """

    try:
        df = yf.download(
            symbol,
            period="3d",
            interval="1h",
            auto_adjust=True,
            progress=False
        )

        if df is None or df.empty:
            return None

        # === DEBUG ===
        print("\n=== DEBUG COLUMNAS ===")
        print("Symbol:", symbol)
        print("COLUMNAS:", df.columns)
        print(df.head())
        print("=====================\n")

        # Detectar MultiIndex y normalizar
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)

        # Normalizar nombres a minúsculas
        df.columns = [c.lower() for c in df.columns]

        # Asegurar que las columnas necesarias existen
        needed = ['open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in needed):
            return None

        # Filtrar solo las necesarias para evitar confusiones
        df = df[needed].copy()
        df.dropna(inplace=True)

        if df.empty:
            return None

        return df

    except Exception as e:
        print(f"[ERROR descarga]: {e}")
        return None


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(period).mean()
    avg_loss = pd.Series(loss).rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def analyze_pair(label, symbol):
    print(f"[{datetime.datetime.now()}] Analizando {label} ({symbol})\n")

    df = download_price(symbol)

    if df is None or len(df) < 50:  # Ajustado a 50 velas
        print("  — Datos insuficientes.\n")
        return

    # Calcular indicadores
    df["ema_fast"] = ema(df["close"], 20)
    df["ema_slow"] = ema(df["close"], 50)
    df["rsi"] = rsi(df["close"], 14)

    last = df.iloc[-1]

    # Señales
    signal = None

    if last["ema_fast"] < last["ema_slow"] and last["rsi"] < 50:
        signal = "SELL"
    elif last["ema_fast"] > last["ema_slow"] and last["rsi"] > 50:
        signal = "BUY"

    if signal:
        price = last["close"]

        if signal == "BUY":
            sl = price * 0.997
            tp = price * 1.003
        else:
            sl = price * 1.003
            tp = price * 0.997

        print(f"SEÑAL {label}: {signal}")
        print(f"  Entrada: {price}")
        print(f"  SL: {sl}")
        print(f"  TP: {tp}\n")

    else:
        print(f"  — No hay señal para {label}\n")


if __name__ == "__main__":
    print(f"[{datetime.datetime.now()}] === Bot PRO ejecutándose (modo CRON) ===\n")

    for label, symbol in FOREX_PAIRS.items():
        analyze_pair(label, symbol)

    print(f"[{datetime.datetime.now()}] === Fin del ciclo PRO ===\n")
