import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

# -------------------------------------------------
# Configuración
# -------------------------------------------------
symbols = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

interval = "15m"
period = "5d"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# -------------------------------------------------
# Función para enviar email
# -------------------------------------------------
def enviar_email(asunto, mensaje):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return

    try:
        msg = MIMEText(mensaje)
        msg["Subject"] = asunto
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()

        print(f"📧 Email enviado: {asunto}")

    except Exception as e:
        print("⚠️ Error enviando email:", e)

# -------------------------------------------------
# Lógica de estrategia (EMA20/EMA50 + RSI + vela)
# -------------------------------------------------
def obtener_senal(df, symbol):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    df["RSI"] = calc_RSI(df["Close"])

    # Última vela
    c0 = df["Close"].iloc[-1]
    o0 = df["Open"].iloc[-1]

    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]
    rsi = df["RSI"].iloc[-1]

    # BUY
    if ema20 > ema50 and rsi > 50 and c0 > o0:
        return "BUY", c0

    # SELL
    if ema20 < ema50 and rsi < 50 and c0 < o0:
        return "SELL", c0

    return None, None

# -------------------------------------------------
# RSI original
# -------------------------------------------------
def calc_RSI(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

# -------------------------------------------------
# Ejecución principal
# -------------------------------------------------
print("=== Bot Intermedio: EMA20/EMA50 + RSI + Vela confirmatoria ===")

for name, yf_symbol in symbols.items():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {name} ({yf_symbol})...")

    df = yf.download(yf_symbol, interval=interval, period=period, progress=False)

    signal, price = obtener_senal(df, name)

    if signal is None:
        print(f"— No hubo señal para {name}")
        continue

    # Mensaje para email
    asunto = f"Señal {signal} {name} — EMA+RSI Confirmada"
    mensaje = f"Se encontró señal {signal} en {name} al precio {price:.5f}"

    enviar_email(asunto, mensaje)

    # Print EXACTO de los logs
    print(f"\nSeñal encontrada: {name} {signal} (entrada {price:.5f})\n")

print("=== Fin ejecución ===")
