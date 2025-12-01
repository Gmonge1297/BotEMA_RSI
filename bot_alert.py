import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from datetime import datetime
import time

# =========================================
# CONFIGURACIÓN
# =========================================
PARES = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

PERIOD = "60d"
INTERVAL = "1h"
LOTE = 0.01
CUENTA = 50.0
RIESGO_MAX_DOLARES = 1.0  # $1 máximo por operación

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# Para no repetir señales
ultima_señal = {}

# =========================================
# ENVIAR EMAIL
# =========================================
def enviar_email(asunto, cuerpo):
    if not all([EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO]):
        print("Faltan credenciales de email")
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo, "plain"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string()))
        print(f"Email enviado → {asunto}")
    except Exception as e:
        print(f"Error enviando email: {e}")

# =========================================
# INDICADORES
# =========================================
def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

# =========================================
# CÁLCULO TP / SL (riesgo $1)
# =========================================
def calcular_tp_sl(precio, direccion, par):
    # Aproximación simple y segura para todos los pares
    if par in ["USDJPY", "XAUUSD"]:
        pip_value = 0.01
    else:
        pip_value = 0.0001

    sl_pips = RIESGO_MAX_DOLARES / (LOTE * 10_000) / pip_value   # ≈ 10-15 pips según par
    sl_pips = max(sl_pips, 12)  # mínimo 12 pips de SL

    if direccion == "BUY":
        sl = round(precio - sl_pips * pip_value, 5)
        tp1 = round(precio + sl_pips * 1.5 * pip_value, 5)
        tp2 = round(precio + sl_pips * 3.0 * pip_value, 5)
    else:  # SELL
        sl = round(precio + sl_pips * pip_value, 5)
        tp1 = round(precio - sl_pips * 1.5 * pip_value, 5)
        tp2 =
