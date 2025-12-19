# bot_alert_pullback.py
import os
import pandas as pd
import numpy as np
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
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

def pip_value(symbol):
    if "XAUUSD" in symbol:
        return 1.0
    return 0.0001

def lot_size(entry, sl, symbol):
    dist = abs(entry - sl)
    if dist <= 0:
        return 0.01
    value_per_point = pip_value(symbol) * 100
    lot = MAX_RISK_USD / (dist * value_per_point / 0.01)
    lot = max(round(lot, 2), 0.01)
    if "XAUUSD" in symbol:
        lot = min(lot, 0.02)
    return lot

def send_email(subject, body):
    if not all([EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO]):
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

def get_data(symbol, timeframe, days):
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now()
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
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df[["open", "high", "low", "close"]]

# ================= LÓGICA PRINCIPAL =================
def analyze(label, symbol):
    print(f"\n→ Analizando {label}...")

    df_h1 = get_data(symbol, "hour", 20)
    time.sleep(3)
    df_m15 = get_data(symbol, "minute", 7)

    if df_h1.empty or df_m15.empty:
        return

    close_h1 = df_h1["close"]
    high_h1  = df_h1["high"]
    low_h1   = df_h1["low"]

    ema50  = ema(close_h1, EMA_FAST)
    ema200 = ema(close_h1, EMA_SLOW)
    atr_v  = atr(high_h1, low_h1, close_h1, ATR_PERIOD)

    trend_up   = ema50.iloc[-1] > ema200.iloc[-1]
    trend_down = ema50.iloc[-1] < ema200.iloc[-1]

    price = close_h1.iloc[-1]
    atr_now = atr_v.iloc[-1]

    # -------- Pullback a EMA 50 --------
    pullback_buy  = trend_up   and abs(price - ema50.iloc[-1]) <= atr_now * 0.4
    pullback_sell = trend_down and abs(price - ema50.iloc[-1]) <= atr_now * 0.4

    if not (pullback_buy or pullback_sell):
        return

    # -------- Confirmación M15 --------
    close_m15 = df_m15["close"]
    open_m15  = df_m15["open"]

    last = -1
    prev = -2

    confirm_buy = (
        pullback_buy and
        close_m15.iloc[last] > open_m15.iloc[last] and
        close_m15.iloc[last] > close_m15.iloc[prev]
    )

    confirm_sell = (
        pullback_sell and
        close_m15.iloc[last] < open_m15.iloc[last] and
        close_m15.iloc[last] < close_m15.iloc[prev]
    )

    if not (confirm_buy or confirm_sell):
        return

    direction = "BUY" if confirm_buy else "SELL"

    sl_dist = max(atr_now * SL_ATR_MULT, 10 if "XAUUSD" in symbol else 0.0010)
    tp_dist = atr_now * TP_ATR_MULT

    entry = price
    sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
    tp = entry + tp_dist if direction == "BUY" else entry - tp_dist

    lot = lot_size(entry, sl, symbol)

    msg = f"""SEÑAL {direction} {label}

Estrategia: Pullback EMA 50/200 + M15
Entrada: {entry:.5f}
SL: {sl:.5f}
TP: {tp:.5f}
Lote: {lot}
Tendencia: {'ALCISTA' if trend_up else 'BAJISTA'}
"""

    send_email(f"{direction} {label}", msg)
    print("SEÑAL ENVIADA")

# ================= MAIN =================
if __name__ == "__main__":
    print(f"=== Bot Pullback EMA 50/200 ({datetime.now().strftime('%H:%M')}) ===")
    for i, (label, symbol) in enumerate(PARES):
        analyze(label, symbol)
        if i < len(PARES) - 1:
            time.sleep(40)
