import yfinance as yf
import pandas as pd
import numpy as np
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from datetime import datetime

# =========================================
# CONFIGURACIONES
# =========================================

PARES = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

PERIOD = "60d"
INTERVAL = "1h"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# =========================================
# FUNCIÓN ENVIAR EMAIL
# =========================================

def enviar_email(asunto, mensaje):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = asunto
        msg.attach(MIMEText(mensaje, "plain"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())

        print(f"📧 Email enviado: {asunto}")

    except Exception as e:
        print(f"⚠️ Error enviando email: {e}")

# =========================================
# CÁLCULO INDICADORES
# =========================================

def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    roll_up = gain.rolling(14).mean()
    roll_down = loss.rolling(14).mean()

    rs = roll_up / roll_down
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

# =========================================
# OBTENER SEÑAL (Opción B: últimas 3 velas)
# =========================================

def obtener_senal(df, nombre_par):
    if len(df) < 50:
        return None

    c = df["Close"].iloc[-3:]     # Cierres últimas 3 velas
    o = df["Open"].iloc[-3:]      # Aperturas últimas 3

    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]
    rsi = df["RSI"].iloc[-1]

    # COMPRA
    condiciones_buy = (
        ema20 > ema50 and
        rsi > 50 and
        (c > o).any()     # Alguna vela alcista
    )

    if condiciones_buy:
        return "BUY", float(df["Close"].iloc[-1])

    # VENTA
    condiciones_sell = (
        ema20 < ema50 and
        rsi < 50 and
        (c < o).any()     # Alguna vela bajista
    )

    if condiciones_sell:
        return "SELL", float(df["Close"].iloc[-1])

    return None

# =========================================
# REVISAR PAR
# =========================================

def revisar_par(nombre, symbol):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {nombre} ({symbol})...")

    df = yf.download(symbol, period=PERIOD, interval=INTERVAL, progress=False)
    if df is None or df.empty:
        print(f"⚠️ No se pudieron descargar datos de {nombre}")
        return

    df = calcular_indicadores(df)
    señal = obtener_senal(df, nombre)

    if señal:
        tipo, entrada = señal
        mensaje = (
            f"Señal confirmada — {nombre} ({tipo})\n"
            f"Entrada: {entrada}\n"
            f"RSI: {df['RSI'].iloc[-1]:.1f}\n"
            f"Bot: EMA20/EMA50 + RSI + 3 velas\n"
            f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        enviar_email(f"Señal {tipo} — {nombre}", mensaje)
        print(f"Señal encontrada: {nombre} {tipo} (entrada {entrada})\n")
    else:
        print(f"— No hubo señal para {nombre}\n")

# =========================================
# EJECUCIÓN PRINCIPAL
# =========================================

print("=== Bot Intermedio: EMA20/EMA50 + RSI + Vela confirmatoria (3 velas) ===\n")

for name, symbol in PARES.items():
    revisar_par(name, symbol)

print("\n=== Fin ejecución ===")
