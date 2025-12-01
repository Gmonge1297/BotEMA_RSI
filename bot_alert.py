import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import os
from email.mime.text import MIMEText
from datetime import datetime

# Parámetros originales
INTERVAL = "5m"
PERIOD = "2d"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# ----------------------------------------------------------
#  Cálculo de indicadores (EMA20/EMA50 + RSI) — con FIX RSI
# ----------------------------------------------------------

def calcular_indicadores(df):
    df = df.copy()

    # Asegurar que Close sea columna 1D float
    df["Close"] = df["Close"].astype(float)

    # EMAs originales
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    # RSI (arreglado)
    delta = df["Close"].diff()

    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, abs(delta), 0)

    # Convertir a 1D
    gain_1d = np.asarray(gain).ravel()
    loss_1d = np.asarray(loss).ravel()

    avg_gain = pd.Series(gain_1d).rolling(14).mean()
    avg_loss = pd.Series(loss_1d).rolling(14).mean()

    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df


# ----------------------------------------------------------
#  Lógica de señal — versión del 22 de noviembre
# ----------------------------------------------------------

def obtener_senal(df, name):
    # Última vela
    c0 = df["Close"].iloc[-1]
    o0 = df["Open"].iloc[-1]

    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]
    rsi = df["RSI"].iloc[-1]

    # Señal de compra original
    if ema20 > ema50 and rsi > 50 and c0 > o0:
        return "buy", c0

    # Señal de venta original
    if ema20 < ema50 and rsi < 50 and c0 < o0:
        return "sell", c0

    return None, None


# ----------------------------------------------------------
#  Enviar email
# ----------------------------------------------------------

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
        print("❌ Error enviando email:", e)


# ----------------------------------------------------------
#  Revisión de cada par
# ----------------------------------------------------------

def revisar_par(name, yf_symbol):
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {name} ({yf_symbol})...")

    df = yf.download(yf_symbol, period=PERIOD, interval=INTERVAL, progress=False)

    if df is None or len(df) < 60:
        print(f"— No hay suficientes datos para {name}")
        return

    df = calcular_indicadores(df)
    signal, price = obtener_senal(df, name)

    if signal is None:
        print(f"— No hubo señal para {name}")
        return

    # Mensaje simple original
    mensaje = f"""
Señal confirmada — {name.upper()} ({signal.upper()})
Entrada: {price:.5f}
RSI: {df['RSI'].iloc[-1]:.1f}
Bot: EMA20/EMA50 + RSI + vela confirmatoria
Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

    enviar_email(f"Señal {signal.upper()} — {name}", mensaje)

    print(f"Señal encontrada: {name} {signal.upper()} (entrada {price:.5f})")


# ----------------------------------------------------------
#  MAIN (pares originales)
# ----------------------------------------------------------

print("=== Bot Intermedio: EMA20/EMA50 + RSI + Vela confirmatoria ===")

pares = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

for name, symbol in pares.items():
    revisar_par(name, symbol)

print("\n=== Fin ejecución ===")
