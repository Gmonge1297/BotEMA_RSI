import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from polygon import RESTClient
import os
import time

# ================= CONFIGURACIÓN =================
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

PARES = [
    ("EURUSD", "C:EURUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("USDJPY", "C:USDJPY"),
    ("AUDUSD", "C:AUDUSD"),
    ("NZDUSD", "C:NZDUSD"),
    ("USDCAD", "C:USDCAD"),
    ("XAUUSD", "C:XAUUSD"),
]

DIAS = 10  # histórico a analizar

# ================= FUNCIONES =================
def ema(series, span):
    return pd.Series(series).astype(float).ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def get_h1(symbol: str, days: int = DIAS) -> pd.DataFrame:
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)
    aggs = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="hour",
        from_=from_date.date(),
        to=to_date.date(),
        limit=50000,
    )
    df = pd.DataFrame(aggs)
    if df.empty:
        return df
    ts_col = "timestamp" if "timestamp" in df.columns else "t"
    df["timestamp"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    col_map = {"o": "open", "h": "high", "l": "low", "c": "close"}
    df = df.rename(columns=col_map)
    return df[["open", "high", "low", "close"]].dropna()

# ================= BACKTEST =================
if __name__ == "__main__":
    print("=== BACKTEST EMA20/50 + RSI (H1, últimas 3 velas) ===")
    for label, symbol in PARES:
        try:
            df = get_h1(symbol)
            if df.empty or len(df) < 60:
                print(f"{label}: ⚠️ Datos insuficientes")
                continue

            ema20 = ema(df["close"], 20)
            ema50 = ema(df["close"], 50)
            rsi_v = rsi(df["close"], 14)

            signals = []
            for i in range(52, len(df)):
                # Tomamos las últimas 3 velas
                c_last3 = df["close"].iloc[i-3:i]
                o_last3 = df["open"].iloc[i-3:i]
                ema20_last3 = ema20.iloc[i-3:i]
                ema50_last3 = ema50.iloc[i-3:i]
                rsi_last3 = rsi_v.iloc[i-3:i]

                # Condiciones de compra
                buy = (
                    all(ema20_last3 > ema50_last3) and
                    all(rsi_last3 > 55) and
                    all(c_last3 > o_last3)
                )

                # Condiciones de venta
                sell = (
                    all(ema20_last3 < ema50_last3) and
                    all(rsi_last3 < 45) and
                    all(c_last3 < o_last3)
                )

                if buy: signals.append("BUY")
                if sell: signals.append("SELL")

            print(f"{label}: {len(signals)} señales en las últimas {len(df)} velas")
            time.sleep(5)  # pausa para evitar rate limit
        except Exception as e:
            print(f"{label}: ⚠️ Error {e}")
