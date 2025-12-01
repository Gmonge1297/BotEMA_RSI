import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import os

# -----------------------------
# Configuración del BOT
# -----------------------------
PERIOD = "5d"
INTERVAL = "5m"

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# -----------------------------
# Enviar correo
# -----------------------------
def send_email(subject, body):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        print(f"📧 Email enviado: {subject}")
    except Exception as e:
        print(f"⚠️ Error enviando correo: {e}")


# -----------------------------
# Calcular indicadores
# -----------------------------
def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    delta = df["Close"].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, abs(delta), 0)

    roll_up = pd.Series(gain).rolling(14).mean()
    roll_down = pd.Series(loss).rolling(14).mean()

    rs = roll_up / roll_down
    df["RSI"] = 100 - (100 / (1 + rs))

    return df


# -----------------------------
# Lógica de señal original
# -----------------------------
def obtener_senal(df, name):

    # Últimos valores (esto evita el error que tienes ahora)
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


# -----------------------------
# Obtener datos y procesar
# -----------------------------
def revisar_par(nombre, yf_symbol):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {nombre} ({yf_symbol})...")

    df = yf.download(yf_symbol, period=PERIOD, interval=INTERVAL, progress=False)

    if df is None or len(df) < 60:
        print("⚠️ Datos insuficientes.")
        return

    df = calcular_indicadores(df)

    signal, price = obtener_senal(df, nombre)

    if signal:
        cuerpo = f"""
Señal confirmada — {nombre} ({signal})
Entrada: {price:.5f}
RSI: {df['RSI'].iloc[-1]:.1f}
Bot: EMA20/EMA50 + RSI + vela confirmatoria
Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        send_email(f"🔔 Señal {signal} {nombre}", cuerpo)

        print(f"Señal encontrada: {nombre} {signal} (entrada {price:.5f})")
    else:
        print(f"— No hubo señal para {nombre}")


# -----------------------------
# Main
# -----------------------------
print("=== Bot Intermedio: EMA20/EMA50 + RSI + Vela confirmatoria ===\n")

for name, symbol in SYMBOLS.items():
    revisar_par(name, symbol)

print("\n=== Fin ejecución ===")
