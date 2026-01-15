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
    rsi_v = rsi(df["close"])
    adx_v = adx(df)

    i = len(df) - 2  # ✅ vela cerrada
    last_ts = df.index[i]

    closes = df["close"].iloc[i-2:i+1]
    opens = df["open"].iloc[i-2:i+1]

    buy = (
        all(ema20.iloc[i-2:i+1] > ema50.iloc[i-2:i+1]) and
        rsi_v.iloc[i-2:i+1].mean() > 50 and
        sum(closes > opens) >= 2
    )

    sell = (
        all(ema20.iloc[i-2:i+1] < ema50.iloc[i-2:i+1]) and
        rsi_v.iloc[i-2:i+1].mean() < 50 and
        sum(closes < opens) >= 2
    )

    # Filtro solo para oro
    if label == "XAUUSD" and adx_v.iloc[i] < 18:
        buy = sell = False

    if not (buy or sell):
        return None, f"{label}: sin señal"

    entry = df["close"].iloc[i]

    if label == "XAUUSD":
        sl_points, tp_points, pip_factor = SL_XAU, TP_XAU, 1
    else:
        sl_points, tp_points, pip_factor = SL_PIPS, TP_PIPS, 0.0001

    lot = calc_lot(sl_points)

    if buy:
        side = "BUY"
        sl = entry - sl_points * pip_factor
        tp = entry + tp_points * pip_factor
    else:
        side = "SELL"
        sl = entry + sl_points * pip_factor
        tp = entry - tp_points * pip_factor

    alert = (
        f"{side} {label}\n\n"
        f"Entrada: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        f"Lote sugerido: {lot}\n"
        f"Riesgo máximo: $1.50\n"
        f"Vela: {last_ts}"
    )

    return alert, f"{label}: {side} confirmado"

# ================= EMAIL =================
def send_email(subject, body):
    msg = MIMEText(body)
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject

    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())

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
