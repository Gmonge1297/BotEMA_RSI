import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import os

# ===========================
# CONFIGURACIÓN GLOBAL
# ===========================

SYMBOLS = {
    "EURUSD": "EURUSD=X",
}

PERIOD = "7d"
INTERVAL = "5m"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# ===========================
# FUNCIÓN: ENVIAR CORREO
# ===========================

def enviar_alerta(asunto, mensaje):
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        print("❌ Variables de email no configuradas.")
        return

    msg = MIMEText(mensaje)
    msg["Subject"] = asunto
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)
        print("📧 Alerta enviada correctamente.")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")


# ===========================
# FUNCIÓN: OBTENER SEÑAL
# ===========================

def obtener_senal(df):
    # Necesitamos al menos 50 velas
    if len(df) < 50:
        return None, None

    # EMAs
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]

    # RSI
    delta = df["Close"].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14).mean().iloc[-1]
    avg_loss = pd.Series(loss).rolling(14).mean().iloc[-1]
    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi = 100 - (100 / (1 + rs))

    # Últimas 3 velas
    c0, o0 = df["Close"].iloc[-1], df["Open"].iloc[-1]
    c1, o1 = df["Close"].iloc[-2], df["Open"].iloc[-2]
    c2, o2 = df["Close"].iloc[-3], df["Open"].iloc[-3]

    # Señal BUY
    if ema20 > ema50 and rsi > 50 and ((c0 > o0) or (c1 > o1) or (c2 > o2)):
        return "BUY", df["Close"].iloc[-1]

    # Señal SELL
    if ema20 < ema50 and rsi < 50 and ((c0 < o0) or (c1 < o1) or (c2 < o2)):
        return "SELL", df["Close"].iloc[-1]

    return None, None


# ===========================
# FUNCIÓN: PROCESAR PAR
# ===========================

def revisar_par(nombre, symbol):
    print(f"\n[🔍 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {nombre} ({symbol})...")
    df = yf.download(symbol, period=PERIOD, interval=INTERVAL, progress=False)

    if df is None or df.empty:
        print(f"❌ No se pudieron descargar datos para {nombre}.")
        return

    señal, precio = obtener_senal(df)

    if señal:
        mensaje = (
            f"Par: {nombre}\n"
            f"Señal: {señal}\n"
            f"Precio actual: {precio}\n"
            f"Hora: {datetime.now()}"
        )
        enviar_alerta(f"⚠️ Señal {señal} - {nombre}", mensaje)
        print(f"✅ Señal detectada: {señal} @ {precio}")
    else:
        print("Sin señal.")


# ===========================
# MAIN
# ===========================

print("\n=== Bot Intermedio: EMA20/EMA50 + RSI + Últimas 3 velas ===")

for name, symbol in SYMBOLS.items():
    revisar_par(name, symbol)
