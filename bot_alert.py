import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import smtplib
from email.mime.text import MIMEText

# ==========================
# CONFIGURACIONES DEL BOT
# ==========================

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

INTERVAL = "1h"
PERIOD = "7d"

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

# ==========================
# CONFIG EMAIL
# ==========================

EMAIL_USER = "TU_CORREO@gmail.com"     
EMAIL_PASS = "TU_APP_PASSWORD"
EMAIL_TO   = "DESTINATARIO@gmail.com"

# ==========================
# FUNCIONES BOT
# ==========================

def log(msg):
    cr = datetime.now(pytz.timezone("America/Costa_Rica"))
    print(f"[ {cr} ] {msg}")

def send_email(subject, body):
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        print("📧 Email enviado exitosamente.")
    except Exception as e:
        print(f"❌ Error enviando email: {e}")

def calculate_indicators(df):
    df["EMA20"] = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()

    delta = df["Close"].diff()

    # ======================================================
    # FIX: asegurar 1D para evitar ValueError de Pandas
    # ======================================================
    gain = np.where(delta > 0, delta, 0).flatten()
    loss = np.where(delta < 0, -delta, 0).flatten()

    avg_gain = pd.Series(gain).rolling(RSI_PERIOD).mean()
    avg_loss = pd.Series(loss).rolling(RSI_PERIOD).mean()

    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

def check_trend_reversal(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    if prev["EMA20"] > prev["EMA50"] and last["EMA20"] < last["EMA50"]:
        return "down"
    if prev["EMA20"] < prev["EMA50"] and last["EMA20"] > last["EMA50"]:
        return "up"

    return None

def check_signal(df):
    last = df.iloc[-1]

    ema20 = last["EMA20"]
    ema50 = last["EMA50"]
    rsi = last["RSI"]
    price = last["Close"]
    bull = last["Close"] > last["Open"]
    bear = last["Close"] < last["Open"]

    if ema20 > ema50 and rsi > 50 and bull:
        return {
            "type": "BUY",
            "price": price,
            "sl": price - 18,
            "tp": price + 36,
            "rsi": rsi
        }

    if ema20 < ema50 and rsi < 50 and bear:
        return {
            "type": "SELL",
            "price": price,
            "sl": price + 18,
            "tp": price - 36,
            "rsi": rsi
        }

    return None

def build_email(name, signal):
    now = datetime.now(pytz.timezone("America/Costa_Rica")).strftime("%Y-%m-%d %H:%M:%S")

    return f"""
Señal confirmada — {name} ({signal['type']})

Entrada: {signal['price']:.5f}
Stop Loss: {signal['sl']:.5f}
Take Profit: {signal['tp']:.5f}
RSI: {signal['rsi']:.1f}
Lote sugerido: 0.01
Riesgo por trade (USD aprox): 1.00

Bot: EMA20/EMA50 + RSI (flex) + vela confirmatoria
Generado: {now}
"""

def build_partial_close_email(name, entry, current):
    now = datetime.now(pytz.timezone("America/Costa_Rica")).strftime("%Y-%m-%d %H:%M:%S")
    profit = current - entry

    return f"""
⚠️ Cerrar ahora — Ganancia parcial recomendada  
{name}

La operación aún iba en positivo, pero se detectó un cambio de tendencia.

Entrada: {entry:.5f}
Precio actual: {current:.5f}
Ganancia actual: {profit:.2f}

Razón: Cruce inverso EMA20/EMA50 detectado.

Generado: {now}
"""

active_trades = {}

def process_symbol(name, symbol):
    global active_trades

    log(f"Descargando datos de {name} ({symbol})...")
    df = yf.download(symbol, interval=INTERVAL, period=PERIOD, progress=False)

    if df is None or df.empty or len(df) < 60:
        print("⚠️ Sin suficientes datos.\n")
        return

    df = calculate_indicators(df)
    signal = check_signal(df)
    price = df.iloc[-1]["Close"]

    # 1) nueva señal
    if signal:
        active_trades[name] = signal
        email = build_email(name, signal)
        send_email(f"Señal — {name}", email)
        return

    # 2) revisar cierres parciales
    if name in active_trades:
        entry = active_trades[name]["price"]
        tp = active_trades[name]["tp"]
        direction = active_trades[name]["type"]

        tp_dist = abs(tp - entry)
        gain_now = abs(price - entry)
        progress = gain_now / tp_dist

        reversal = check_trend_reversal(df)

        if progress >= 0.70:
            if (direction == "BUY" and reversal == "down") or \
               (direction == "SELL" and reversal == "up"):

                email = build_partial_close_email(name, entry, price)
                send_email(f"⚠️ Cerrar con ganancia parcial — {name}", email)
                del active_trades[name]
                return

    print("ℹ️ Sin señales nuevas.\n")

# ======================
# MAIN
# ======================

if __name__ == "__main__":
    print("\n=== Bot EMA+RSI (Actualizado con alerta de cierre parcial) ===\n")

    for name, symbol in SYMBOLS.items():
        process_symbol(name, symbol)

    print("\n=== Fin ejecución ===\n")
