import yfinance as yf
import pandas as pd
import numpy as np
import smtplib, ssl
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
RIESGO_MAX_DOLARES = 1.0

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

ultima_señal = {}

# =========================================
# ENVIAR EMAIL (corregida)
# =========================================
def enviar_email(asunto, cuerpo):
    if not all([EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO]):
        print("Faltan credenciales")
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
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        print(f"Email enviado: {asunto}")
    except Exception as e:
        print(f"Error email: {e}")

# =========================================
# INDICADORES
# =========================================
def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df["RSI"] = 100 - (100 / (1 + rs.where(loss != 0, 1))
    return df

# =========================================
# TP / SL simple y seguro
# =========================================
def calcular_tp_sl(precio, direccion):
    if "JPY" in direccion or "XAU" in direccion:
        pip = 0.01
    else:
        pip = 0.0001
    sl_pips = max(12, RIESGO_MAX_DOLARES / (LOTE * 10000) / pip)
    if direccion == "BUY":
        sl = round(precio - sl_pips * pip, 5)
        tp = round(precio + sl_pips * 2 * pip, 5)   # 2R clásico
    else:
        sl = round(precio + sl_pips * pip, 5)
        tp = round(precio - sl_pips * 2 * pip, 5)
    return sl, tp

# =========================================
# SEÑAL MÁS FRECUENTE (como cuando te dio 6/6)
# =========================================
def obtener_senal(df, par):
    if len(df) < 60:
        return None

    ultimo = df.iloc[-1]
    ultimas_3 = df.iloc[-3:]

    ema20 = ultimo["EMA20"]
    ema50 = ultimo["EMA50"]
    rsi = ultimo["RSI"]
    precio = ultimo["Close"]

    alcistas_3 = (ultimas_3["Close"] > ultimas_3["Open"]).sum()
    bajistas_3 = (ultimas_3["Close"] < ultimas_3["Open"]).sum()

    # BUY: tendencia alcista + al menos 2 de las últimas 3 velas verdes
    if (ema20 > ema50 and rsi > 50 and rsi < 75 and alcistas_3 >= 2):
        direccion = "BUY"
    # SELL: tendencia bajista + al menos 2 de las últimas 3 velas rojas
    elif (ema20 < ema50 and rsi < 50 and rsi > 25 and bajistas_3 >= 2):
        direccion = "SELL"
    else:
        return None

    # Anti-repetición 4 horas
    if par in ultima_señal:
        if (ultima_señal[par]["dir"] == direccion and
            (datetime.now() - ultima_señal[par]["time"]).total_seconds() < 14400):
            return None

    sl, tp = calcular_tp_sl(precio, direccion)
    ultima_señal[par] = {"dir": direccion, "time": datetime.now()}

    return {
        "dir": direccion,
        "entrada": round(precio, 5),
        "sl": sl,
        "tp": tp,
        "rsi": round(rsi, 1)
    }

# =========================================
# LOOP
# =========================================
print("BOT EMA20/50 + RSI + 2 de 3 velas – Iniciado\n")

for nombre, symbol in PARES.items():
    try:
        df = yf.download(symbol, period=PERIOD, interval=INTERVAL,
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < 60: continue

        df = calcular_indicadores(df)
        señal = obtener_senal(df, nombre)

        if señal:
            cuerpo = f"""Señal confirmada — {nombre} ({señal['dir']})

Entrada: {señal['entrada']}
Stop Loss: {señal['sl']}
Take Profit: {señal['tp']}
RSI: {señal['rsi']}
Lote sugerido: 0.01
Riesgo por trade (USD aprox): 1.00
Bot: EMA20/EMA50 + RSI (flex) + 2 de 3 velas

Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

            enviar_email(f"Señal {señal['dir']} — {nombre}", cuerpo)
            print(f"SEÑAL {señal['dir']} {nombre}")

    except Exception as e:
        print(f"Error {nombre}: {e}")
    time.sleep(2)

print("\nFin de ejecución")
