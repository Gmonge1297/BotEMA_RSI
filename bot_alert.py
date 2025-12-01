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
RIESGO_MAX_USD = 1.0

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

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
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        print(f"Email enviado: {asunto}")
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
    # ganancia promedio
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()  # pérdida promedio
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(50, inplace=True)  # evitar NaN al inicio

    return df

# =========================================
# TP / SL (riesgo ≈ $1)
# =========================================
def calcular_tp_sl(precio, direccion, par):
    if "JPY" in par or "XAU" in par:
        pip = 0.01
    else:
        pip = 0.0001

    # Aproximadamente 12–20 pips de SL según par → riesgo ≈ $1 con lote 0.01
    sl_pips = max(12, round(RIESGO_MAX_USD / (LOTE * 10000 * pip), 1))

    if direccion == "BUY":
        sl = round(precio - sl_pips * pip, 5)
        tp = round(precio + sl_pips * 2 * pip, 5)   # Reward 2R
    else:
        sl = round(precio + sl_pips * pip, 5)
        tp = round(precio - sl_pips * 2 * pip, 5)

    return sl, tp

# =========================================
# SEÑAL (versión que te dio 6/6 ganadoras)
# =========================================
def obtener_senal(df, par):
    if len(df) < 60:
        return None

    u = df.iloc[-1]        # última vela
    ultimas_3 = df.iloc[-3:]

    ema20 = u["EMA20"]
    ema50 = u["EMA50"]
    rsi = u["RSI"]
    precio = u["Close"]

    velas_alcistas = (ultimas_3["Close"] > ultimas_3["Open"]).sum()
    velas_bajistas = (ultimas_3["Close"] < ultimas_3["Open"]).sum()

    # BUY
    if ema20 > ema50 and rsi > 50 and rsi < 75 and velas_alcistas >= 2:
        direccion = "BUY"
    # SELL
    elif ema20 < ema50 and rsi < 50 and rsi > 25 and velas_bajistas >= 2:
        direccion = "SELL"
    else:
        return None

    # No repetir la misma señal en menos de 4 horas
    key = par
    if key in ultima_señal:
        if (ultima_señal[key]["dir"] == direccion and
            (datetime.now() - ultima_señal[key]["time"]).total_seconds() < 14400):
            return None

    sl, tp = calcular_tp_sl(precio, direccion, par)

    ultima_señal[key] = {"dir": direccion, "time": datetime.now()}

    return {
        "dir": direccion,
        "entrada": round(precio, 5),
        "sl": sl,
        "tp": tp,
        "rsi": round(rsi, 1)
    }

# =========================================
# LOOP PRINCIPAL
# =========================================== ========================
print("BOT FOREX – EMA20/50 + RSI + 2 de 3 velas – Iniciado\n")

for nombre, symbol in PARES.items():
    try:
        print(f"Analizando {nombre}...")
        df = yf.download(symbol, period=PERIOD, interval=INTERVAL,
                         progress=False, auto_adjust=True, threads=False)

        if df.empty or len(df) < 60:
            print(f"  Sin datos → {nombre}\n")
            continue

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
Bot: EMA20/EMA50 + RSI (flex) + 2 de 3 velas confirmatorias

Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

            enviar_email(f"Señal {señal['dir']} — {nombre}", cuerpo)
            print(f"  SEÑAL ENVIADA → {señal['dir']} {nombre}\n")
        else:
            print(f"  Sin señal → {nombre}\n")

        time.sleep(2)

    except Exception as e:
        print(f"Error {nombre}: {e}")

print("\nEjecución finalizada – esperando próxima hora")
