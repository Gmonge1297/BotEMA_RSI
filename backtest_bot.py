import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from polygon import RESTClient
import os

# ================= CONFIGURACIÓN =================
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

# Lista de pares a analizar
PARES = [
    ("EURUSD", "C:EURUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("USDJPY", "C:USDJPY"),
    ("AUDUSD", "C:AUDUSD"),
    ("NZDUSD", "C:NZDUSD"),
    ("USDCAD", "C:USDCAD"),
    ("XAUUSD", "C:XAUUSD"),
]

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

def get_h1(symbol: str, days: int = 10) -> pd.DataFrame:
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)
    aggs = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="hour",   # timeframe H1
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
    print("=== BACKTEST EMA20/50 + RSI (H1) ===")
    for label, symbol in PARES:
        try:
            df = get_h1(symbol, days=10)  # últimos 10 días (~240 velas H1)
            if df.empty or len(df) < 60:
                print(f"{label}: ⚠️ Datos insuficientes")
                continue

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
        except Exception as e:
            print(f"{label}: ⚠️ Error {e}")
