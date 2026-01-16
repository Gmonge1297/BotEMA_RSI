import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from polygon import RESTClient
import os
import smtplib
from email.mime.text import MIMEText
import time

# ================= CONFIGURACIÓN =================
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

PARES = [
    ("USDJPY", "C:USDJPY"),
    ("NZDUSD", "C:NZDUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("XAUUSD", "C:XAUUSD"),
]

# SL / TP
TP_PIPS = 30
SL_PIPS = 20

TP_XAU = 800
SL_XAU = 500

# 🔒 RIESGO FIJO
FIXED_RISK_USD = 1.50
PIP_VALUE_PER_LOT = 10  # FX estándar

# Email
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

# ================= INDICADORES =================
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def adx(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0)
    minus_dm = np.where((down > up) & (down > 0), down, 0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    return dx.rolling(period).mean().fillna(20)

# ================= DATOS =================
def get_h1(symbol, days=10):
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

    # 🔑 Polygon puede devolver 't' o 'timestamp'
    if "t" in df.columns:
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    else:
        raise ValueError("No se encontró columna de timestamp en Polygon")

    df.set_index("timestamp", inplace=True)

    df = df.rename(columns={
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close"
    })

    return df[["open", "high", "low", "close"]].dropna()

# ================= RIESGO =================
def calc_lot(sl_pips):
    lot = FIXED_RISK_USD / (sl_pips * PIP_VALUE_PER_LOT)
    return round(lot, 2)

# ================= SEÑAL =================
def current_signal(label, symbol):
    df = get_h1(symbol)

    if df.empty or len(df) < 60:
        return None, f"{label}: datos insuficientes"

    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    rsi_v = rsi(df["close"], 14)
    adx_v = adx(df)

    i = len(df) - 1
    candle = df.iloc[i]

    open_p = candle["open"]
    high_p = candle["high"]
    low_p = candle["low"]
    close_p = candle["close"]
    ts = df.index[i]

    # Contexto últimas 3 velas
    ema20_3 = ema20.iloc[i-3:i]
    ema50_3 = ema50.iloc[i-3:i]
    rsi_3 = rsi_v.iloc[i-3:i]
    closes_3 = df["close"].iloc[i-3:i]
    opens_3 = df["open"].iloc[i-3:i]

    buy = (
        all(ema20_3 > ema50_3) and
        rsi_3.mean() > 50 and
        sum(closes_3 > opens_3) >= 2
    )

    sell = (
        all(ema20_3 < ema50_3) and
        rsi_3.mean() < 50 and
        sum(closes_3 < opens_3) >= 2
    )

    # Filtro oro con ADX
    if label == "XAUUSD" and adx_v.rolling(3).mean().iloc[i] < 18:
        buy = sell = False

    if not (buy or sell):
        return None, f"{label}: sin señal"

    # Parámetros
    if label == "XAUUSD":
        pip_factor = 1.0
        sl_pips = SL_XAU
        tp_pips = TP_XAU
    else:
        pip_factor = 0.0001
        sl_pips = SL_PIPS
        tp_pips = TP_PIPS

    entry = close_p

    # Confirmación SEMI-LIVE (toque intra-vela)
    if buy and low_p > entry:
        return None, f"{label}: BUY no tocado por precio"

    if sell and high_p < entry:
        return None, f"{label}: SELL no tocado por precio"

    # Precio actual (último close disponible)
    current_price = close_p

    # No entrar tarde (máx 25% del SL)
    max_deviation = sl_pips * pip_factor * 0.25

    if buy and abs(current_price - entry) > max_deviation:
        return None, f"{label}: BUY descartado (precio lejos)"

    if sell and abs(current_price - entry) > max_deviation:
        return None, f"{label}: SELL descartado (precio lejos)"

    # SL / TP
    if buy:
        sl = entry - sl_pips * pip_factor
        tp = entry + tp_pips * pip_factor
        alert = format_alert(label, "BUY", entry, tp, sl, rsi_v.iloc[i], pip_factor)
        return alert, f"{label}: BUY confirmado (vela {ts})"

    if sell:
        sl = entry + sl_pips * pip_factor
        tp = entry - tp_pips * pip_factor
        alert = format_alert(label, "SELL", entry, tp, sl, rsi_v.iloc[i], pip_factor)
        return alert, f"{label}: SELL confirmado (vela {ts})"
# ================= MAIN =================
if __name__ == "__main__":
    print("=== BOT EMA20/50 + RSI | RIESGO FIJO $1.50 ===")
    time.sleep(30)

    any_alert = False

    for label, symbol in PARES:
        try:
            alert, status = current_signal(label, symbol)
            print(status)

            if alert:
                any_alert = True
                send_email(f"Señal {label}", alert)

        except Exception as e:
            print(f"{label}: error {e}")

    if not any_alert:
        print("Sin señales recientes.")
