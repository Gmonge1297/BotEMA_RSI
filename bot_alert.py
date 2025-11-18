import yfinance as yf
import pandas as pd
import numpy as np
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ==========================
# CONFIGURACIÓN
# ==========================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

PARES = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "XAUUSD=X"]

RSI_PERIOD = 14
RSI_BUY = 50
RSI_SELL = 50


# ==========================
# RSI
# ==========================
def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    roll_up = pd.Series(gain).rolling(period).mean()
    roll_down = pd.Series(loss).rolling(period).mean()

    RS = roll_up / roll_down
    return 100 - (100 / (1 + RS))


# ==========================
# FUNCIÓN DE SEÑALES
# ==========================
def generar_senal(df, par):
    # --- FIX MULTIINDEX ---
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)

    # --- VALIDAR COLUMNAS ---
    required = ["Open", "High", "Low", "Close"]
    for col in required:
        if col not in df.columns:
            print(f"Falta columna {col} en {par}")
            return None

    # --- EMAs ---
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    # --- RSI ---
    df["RSI"] = rsi(df["Close"], RSI_PERIOD)

    # --- TOMAR ÚLTIMAS VELAS COMO SERIES (FUNDAMENTAL) ---
    c = df.iloc[-1].astype(float)  # vela actual
    p = df.iloc[-2].astype(float)  # vela previa

    # --- CRUCES ---
    cruce_up = (p["EMA20"] <= p["EMA50"]) and (c["EMA20"] > c["EMA50"])
    cruce_down = (p["EMA20"] >= p["EMA50"]) and (c["EMA20"] < c["EMA50"])

    # --- VELAS ---
    vela_verde = c["Close"] > c["Open"]
    vela_roja = c["Close"] < c["Open"]

    # --- BUY ---
    if cruce_up and vela_verde and c["RSI"] > RSI_BUY:
        return {
            "tipo": "BUY",
            "entrada": float(c["Close"]),
            "par": par
        }

    # --- SELL ---
    if cruce_down and vela_roja and c["RSI"] < RSI_SELL:
        return {
            "tipo": "SELL",
            "entrada": float(c["Close"]),
            "par": par
        }

    return None


# ==========================
# EMAIL
# ==========================
def enviar_alerta(senal):
    subject = f"Alerta {senal['tipo']} - {senal['par']}"
    body = f"""
Se generó una señal:

PAR: {senal['par']}
TIPO: {senal['tipo']}
ENTRADA: {senal['entrada']}

Bot EMA20/EMA50 + RSI + Confirmación de Vela
"""

    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print(f"Correo enviado: {subject}")
    except Exception as e:
        print(f"Error enviando correo: {e}")


# ==========================
# MAIN LOOP
# ==========================
if __name__ == "__main__":
    print("=== BOT EJECUTÁNDOSE (YFINANCE) ===\n")

    for par in PARES:
        print(f"\nDescargando datos de {par}...\n")

        df = yf.download(par, interval="1h", period="7d")

        if df is None or len(df) < 50:
            print("Datos insuficientes.")
            continue

        senal = generar_senal(df, par)

        if senal:
            enviar_alerta(senal)
        else:
            print("No hay señal en esta hora.")
