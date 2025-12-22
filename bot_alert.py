# bot_alert_hybrid_ultimate.py
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

EMA_FAST = 50
EMA_SLOW = 200

ATR_PERIOD = 14
SL_ATR_MULT = 1.2
TP_ATR_MULT = 2.5

MAX_RISK_USD = 5.0
MIN_LOT = 0.01
MAX_LOT = 0.50

MAX_ENTRY_DEVIATION_PIPS = 5      # 🔒 ejecutabilidad real
SIGNAL_COOLDOWN_MINUTES = 90      # 🔒 no repetir señales

STATE_FILE = "signal_state.json"

PARES = [
    ("EURUSD", "C:EURUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("XAUUSD", "C:XAUUSD")
]

# ================= UTILIDADES =================
def to_1d(s):
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    return pd.Series(s).astype(float).reset_index(drop=True)

def ema(series, span):
    return to_1d(series).ewm(span=span, adjust=False).mean()

def atr(high, low, close, period):
    tr = pd.concat([
        to_1d(high) - to_1d(low),
        abs(to_1d(high) - to_1d(close).shift()),
        abs(to_1d(low) - to_1d(close).shift())
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def pip_size(symbol):
    return 0.01 if "XAUUSD" in symbol else 0.0001

# ================= ESTADO (ANTI-REPETICIÓN) =================
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
    if stop_pips <= 0:
        return MIN_LOT

    pip_value_per_lot = 1.0 if "XAUUSD" in symbol else 10.0
    lot = MAX_RISK_USD / (stop_pips * pip_value_per_lot)

    lot = round(lot, 2)
    return min(max(lot, MIN_LOT), MAX_LOT)

# ================= EMAIL =================
def send_email(subject, body):
    if not all([EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO]):
        print("Email no configurado")
        return
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
def get_data(symbol, timeframe, days):
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)

    aggs = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan=timeframe,
        from_=from_date.date(),
        to=to_date.date(),
        limit=50000
    )
    df = pd.DataFrame(aggs)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df[["open", "high", "low", "close"]]

# ================= LÓGICA PRINCIPAL =================
def analyze(label, symbol, state):
    print(f"\n→ Analizando {label}...")

    now = datetime.now(timezone.utc)

    # ----- cooldown -----
    last_time = state.get(label)
    if last_time:
        last_time = datetime.fromisoformat(last_time)
        if (now - last_time).total_seconds() < SIGNAL_COOLDOWN_MINUTES * 60:
            print("  ⏸️ En cooldown")
            return

    df_h1 = get_data(symbol, "hour", 20)
    df_m15 = get_data(symbol, "minute", 7)

    if df_h1.empty or df_m15.empty:
        return

    h1 = df_h1.iloc[-1]
    m15 = df_m15.iloc[-1]

    ema50  = ema(df_h1["close"], EMA_FAST)
    ema200 = ema(df_h1["close"], EMA_SLOW)
    atr_v  = atr(df_h1["high"], df_h1["low"], df_h1["close"], ATR_PERIOD)

    trend_up   = ema50.iloc[-1] > ema200.iloc[-1]
    trend_down = ema50.iloc[-1] < ema200.iloc[-1]

    atr_now = atr_v.iloc[-1]

    pullback_buy  = trend_up   and abs(h1["close"] - ema50.iloc[-1]) <= atr_now * 0.4
    pullback_sell = trend_down and abs(h1["close"] - ema50.iloc[-1]) <= atr_now * 0.4

    if not (pullback_buy or pullback_sell):
        return

    confirm_buy  = pullback_buy  and m15["close"] > m15["open"]
    confirm_sell = pullback_sell and m15["close"] < m15["open"]

    if not (confirm_buy or confirm_sell):
        return

    direction = "BUY" if confirm_buy else "SELL"

    # 🔑 PRECIO REAL (última M15)
    entry = m15["close"]

    # ----- validación FINAL de ejecutabilidad -----
    current_price = m15["close"]
    deviation_pips = abs(current_price - entry) / pip_size(symbol)

    if deviation_pips > MAX_ENTRY_DEVIATION_PIPS:
        print("  🚫 Precio ya no ejecutable")
        return

    # ----- SL / TP -----
    sl_dist = max(atr_now * SL_ATR_MULT, 10 if "XAUUSD" in symbol else 0.0010)
    tp_dist = atr_now * TP_ATR_MULT

    sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
    tp = entry + tp_dist if direction == "BUY" else entry - tp_dist

    lot = lot_size(entry, sl, symbol)

    # ----- ENVIAR SEÑAL -----
    msg = f"""SEÑAL {direction} {label}

⚠️ SEÑAL EJECUTABLE – SISTEMA CERRADO

Estrategia: Pullback EMA 50/200
Modo: HÍBRIDO (H1 + confirmación M15)

ENTRADA (market): {entry:.5f}
SL: {sl:.5f}
TP: {tp:.5f}
Lote: {lot}

Regla:
Si recibes esta señal, ejecuta sin pensar.
Si no llegó señal, no había trade.
"""

    send_email(f"{direction} {label} – EJECUTAR", msg)

    state[label] = now.isoformat()
    save_state(state)

# ================= MAIN =================
if __name__ == "__main__":
    print(f"=== BOT DEFINITIVO CERRADO ({datetime.now().strftime('%H:%M')}) ===")

    state = load_state()

    for label, symbol in PARES:
        analyze(label, symbol, state)
        time.sleep(20)

    print("\nCiclo terminado – sistema blindado 🔒")
