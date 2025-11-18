import pandas as pd
import numpy as np
import datetime as dt
import pytz
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yfinance as yf
import os


# ===============================
# CONFIGURACIÓN GENERAL
# ===============================

CR_TZ = pytz.timezone("America/Costa_Rica")

# Credenciales desde Secrets de GitHub
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = "edgardoms2010@gmail.com"   # <- Cambiar si deseas

# Parámetros estrategia
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

SL_PIPS = 300
TP_PIPS = 600
MAX_RISK_USD = 1.5

# Pares versión Yahoo
pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}


# ===============================
# INDICADORES
# ===============================

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ===============================
# ENVÍO DE EMAIL
# ===============================

def enviar_correo(asunto, mensaje):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = asunto
        msg.attach(MIMEText(mensaje, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()

        print("Correo enviado:", asunto)
    except Exception as e:
        print("ERROR enviando correo:", e)


# ===============================
# GENERAR SEÑAL
# ===============================

def generar_senal(df, par):
    # EMA
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    # RSI
    df["RSI"] = rsi(df["Close"], RSI_PERIOD)

    c = df.iloc[-1]     # última vela
    p = df.iloc[-2]     # vela anterior

    # CRUCE
    cruce_up = p["EMA20"] <= p["EMA50"] and c["EMA20"] > c["EMA50"]
    cruce_down = p["EMA20"] >= p["EMA50"] and c["EMA20"] < c["EMA50"]

    # Velas
    vela_verde = c["Close"] > c["Open"]
    vela_roja = c["Close"] < c["Open"]

    # Señal BUY
    if cruce_up and vela_verde and c["RSI"] > RSI_BUY:
        return {
            "tipo": "BUY",
            "entrada": float(c["Close"]),
        }

    # Señal SELL
    if cruce_down and vela_roja and c["RSI"] < RSI_SELL:
        return {
            "tipo": "SELL",
            "entrada": float(c["Close"]),
        }

    return None


# ===============================
# LOOP PRINCIPAL
# ===============================

def pip_value(symbol):
    if "JPY" in symbol:
        return 0.01
    if "XAU" in symbol:
        return 0.1
    return 0.0001


if __name__ == "__main__":
    print("=== BOT EJECUTÁNDOSE (YFINANCE) ===\n")

    for par, ticker in pairs.items():
        print(f"\nDescargando datos de {ticker}...")

        df = yf.download(ticker, interval="1h", period="7d")
        if df.empty:
            print("Sin datos suficientes.")
            continue

        senal = generar_senal(df, par)

        if senal:
            entry = senal["entrada"]
            pv = pip_value(par)

            if senal["tipo"] == "BUY":
                sl = entry - SL_PIPS * pv
                tp = entry + TP_PIPS * pv
            else:
                sl = entry + SL_PIPS * pv
                tp = entry - TP_PIPS * pv

            mensaje = f"""
🔔 Señal {senal['tipo']} CONFIRMADA en {par}

Entrada: {entry}
Stop Loss: {sl}
Take Profit: {tp}
RSI: {df['RSI'].iloc[-1]:.1f}
"""

            enviar_correo(f"{senal['tipo']} Confirmado {par}", mensaje)

        time.sleep(1)

    print("\nNo hubo más señales esta hora.\n")
