# bot_alert_ema20_50_rsi.py
import os
import pandas as pd
import numpy as np
import smtplib
import time
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from polygon import RESTClient

# ================= CONFIG =================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

SL_PIPS = 30
TP_PIPS = 30
MAX_RISK_USD = 1.50

COOLDOWN_HOURS = 2
STATE_FILE = "ema_state.json"

PARES = [
    ("EURUSD", "C:EURUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("USDJPY", "C:USDJPY"),
]

# ================= UTILIDADES =================
def pip_size(symbol):
    if "JPY" in symbol:
        return 0.01
    if "XAUUSD" in symbol:
        return 0.1
    return 0.0001

def to_1d(s):
    return pd.Series(s).astype(float).reset_index(drop=True)

def ema(series, span):
    return to_1d(series).ewm(span=span, adjust=False).mean()

def rsi(series, period):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

# ================= ESTADO =================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ================= LOTAJE =================
def lot_size(entry, sl, symbol):
    pip_sz = pip_size(symbol)
    stop_pips = abs(entry - sl) / pip_sz

    if "JPY" in symbol:
        pip_value = 9.0
    else:
        pip_value = 10.0

    lot = MAX_RISK_USD / (stop_pips * pip_value)
    return max(round(lot, 2), 0.01)

# ================= EMAIL =================
def send_email(subject, body):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(EMAIL_USER, EMAIL_PASSWORD)
    server.send_message(msg)
    server.quit()
    print("EMAIL ENVIADO")

# ================= DATOS =================
def get_h1(symbol, days=20):
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)

    aggs = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="hour",
        from_=from_date.date(),
        to=to_date.date(),
        limit=50000
    )

    df = pd.DataFrame(aggs)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df[["open", "high", "low", "close"]]

# ================= LÓGICA =================
def analyze(label, symbol, state):
    print(f"\n→ Analizando {label}...")

    now = datetime.now(timezone.utc)
    last = state.get(label)
    if last and (now - datetime.fromisoformat(last)).total_seconds() < COOLDOWN_HOURS * 3600:
        print("  ⏸️ Cooldown activo")
        return

    df = get_h1(symbol)
    if len(df) < 60:
        return

    close = df["close"]
    open_ = df["open"]

    ema20 = ema(close, EMA_FAST)
    ema50 = ema(close, EMA_SLOW)
    rsi_v = rsi(close, RSI_PERIOD)

    # vela actual y previa
    c0, o0 = close.iloc[-1], open_.iloc[-1]
    c1 = close.iloc[-2]

    buy = (
        ema20.iloc[-2] <= ema50.iloc[-2] and
        ema20.iloc[-1] > ema50.iloc[-1] and
        rsi_v.iloc[-1] > 50 and
        c0 > o0 and
        c0 <= max(close.iloc[-5:-1])  # no ruptura
    )

    sell = (
        ema20.iloc[-2] >= ema50.iloc[-2] and
        ema20.iloc[-1] < ema50.iloc[-1] and
        rsi_v.iloc[-1] < 50 and
        c0 < o0 and
        c0 >= min(close.iloc[-5:-1])
    )

    if not (buy or sell):
        return

    direction = "BUY" if buy else "SELL"
    entry = c0

    pip_sz = pip_size(symbol)
    sl = entry - SL_PIPS * pip_sz if buy else entry + SL_PIPS * pip_sz
    tp = entry + TP_PIPS * pip_sz if buy else entry - TP_PIPS * pip_sz

    lot = lot_size(entry, sl, symbol)

    msg = f"""SEÑAL {direction} {label}

Estrategia: EMA 20/50 + RSI
Timeframe: H1

Entrada (market): {entry:.5f}
SL: {sl:.5f}
TP: {tp:.5f}
Lote: {lot}

Regla:
Cruce confirmado + RSI filtrado.
Si llega esta señal, se ejecuta.
"""

    send_email(f"{direction} {label}", msg)
    state[label] = now.isoformat()
    save_state(state)

# ================= MAIN =================
if __name__ == "__main__":
    print(f"=== BOT EMA 20/50 + RSI ({datetime.now().strftime('%H:%M')}) ===")
    state = load_state()

    for label, symbol in PARES:
        analyze(label, symbol, state)
        time.sleep(20)

    print("\nCiclo terminado 🚀")
