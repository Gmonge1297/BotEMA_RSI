import os
import smtplib
import pandas as pd
import yfinance as yf
from email.mime.text import MIMEText
from datetime import datetime

# ============================
# CONFIGURACIÓN GENERAL
# ============================

PARES = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

PERIOD = "5d"
INTERVAL = "1h"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")


# ============================
# FUNCIÓN PARA ENVIAR CORREO
# ============================

def enviar_email(asunto, mensaje):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return

    msg = MIMEText(mensaje)
    msg["Subject"] = asunto
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print(f"📧 Email enviado: {asunto}")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")


# ============================
# CÁLCULO DE INDICADORES
# ============================

def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    roll_up = gain.rolling(14).mean()
    roll_down = loss.rolling(14).mean()

    RS = roll_up / roll_down
    df["RSI"] = 100 - (100 / (1 + RS))

    df = df.dropna()
    return df


# ============================
# REGLAS DE SEÑAL (3 velas)
# ============================

def obtener_senal(df):
    c0, o0 = df["Close"].iloc[-1], df["Open"].iloc[-1]
    c1, o1 = df["Close"].iloc[-2], df["Open"].iloc[-2]
    c2, o2 = df["Close"].iloc[-3], df["Open"].iloc[-3]

    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]
    rsi = df["RSI"].iloc[-1]

    # BUY: Tendencia alcista + RSI > 50 + al menos 1 de las 3 velas sea alcista
    if ema20 > ema50 and rsi > 50 and ((c0 > o0) or (c1 > o1) or (c2 > o2)):
        return "BUY", c0

    # SELL: Tendencia bajista + RSI < 50 + al menos 1 de las 3 velas sea bajista
    if ema20 < ema50 and rsi < 50 and ((c0 < o0) or (c1 < o1) or (c2 < o2)):
        return "SELL", c0

    return None, None


# ============================
# PROCESO PARA CADA PAR
# ============================

def revisar_par(nombre, symbol):

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {nombre} ({symbol})...")

    df = yf.download(symbol, period=PERIOD, interval=INTERVAL, progress=False)
    if df is None or df.empty:
        print("❌ No se pudieron descargar datos.")
        return

    df = calcular_indicadores(df)

    señal, precio = obtener_senal(df)

    if señal:
        asunto = f"🔥 Señal {señal} {nombre}"
        mensaje = f"Señal detectada en {nombre}\nDirección: {señal}\nPrecio: {precio}"
        enviar_email(asunto, mensaje)
        print(f"➡️ Señal encontrada: {nombre} {señal} (precio {precio})")
    else:
        print(f"— No hubo señal para {nombre}")


# ============================
# EJECUCIÓN PRINCIPAL
# ============================

print("=== Bot Intermedio: EMA20/EMA50 + RSI + Últimas 3 velas ===")

for name, symbol in PARES.items():
    revisar_par(name, symbol)

print("\n=== Fin ejecución ===")
