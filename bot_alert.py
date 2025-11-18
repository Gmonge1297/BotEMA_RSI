import yfinance as yf
import pandas as pd
import numpy as np
import time
import os
import smtplib
from email.mime.text import MIMEText

# =========================================================
# CONFIGURACIÓN EMAIL
# =========================================================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# =========================================================
# FUNCIÓN PARA ENVIAR CORREOS
# =========================================================
def enviar_correo(asunto, mensaje):
    try:
        msg = MIMEText(mensaje)
        msg["Subject"] = asunto
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())

        print("Correo enviado ✔")
    except Exception as e:
        print("Error enviando correo:", e)


# =========================================================
# DESCARGA DE DATOS (ANTI BLOQUEO)
# =========================================================
def get_data(par):
    try:
        print(f"\nDescargando datos de {par}...")
        df = yf.download(par, interval="1h", period="7d")

        # Si no devuelve datos
        if df is None or df.empty:
            print("⚠ Sin datos suficientes.")
            return None

        df = df.dropna()
        return df

    except Exception as e:
        print("⚠ Error descargando datos:", e)
        return None


# =========================================================
# CÁLCULO DE INDICADORES
# =========================================================
def aplicar_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()

    # RSI manual
    delta = df["Close"].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    rs = up.rolling(14).mean() / down.rolling(14).mean()
    df["RSI"] = 100 - (100 / (1 + rs))

    df.dropna(inplace=True)
    return df


# =========================================================
# ESTRATEGIA
# =========================================================
def generar_senal(df, par):

    c = df.iloc[-1]  # última vela
    prev = df.iloc[-2]  # vela anterior

    # Condiciones BUY
    buy = (
        c["EMA20"] > c["EMA50"] and
        prev["EMA20"] <= prev["EMA50"] and
        c["RSI"] > 50 and
        c["Close"] > c["Open"]  # vela verde
    )

    # Condiciones SELL
    sell = (
        c["EMA20"] < c["EMA50"] and
        prev["EMA20"] >= prev["EMA50"] and
        c["RSI"] < 50 and
        c["Close"] < c["Open"]  # vela roja
    )

    if buy:
        entry = float(c["Close"])
        sl = round(entry - (entry * 0.003), 5)  # 30 pips approx
        tp = round(entry + (entry * 0.006), 5)  # 60 pips approx

        return f"""📈 **SEÑAL DE COMPRA ({par})**

Entrada: {entry}
Stop Loss: {sl}
Take Profit: {tp}

Condiciones:
- EMA20 > EMA50
- RSI > 50
- Vela verde de confirmación
"""

    if sell:
        entry = float(c["Close"])
        sl = round(entry + (entry * 0.003), 5)
        tp = round(entry - (entry * 0.006), 5)

        return f"""📉 **SEÑAL DE VENTA ({par})**

Entrada: {entry}
Stop Loss: {sl}
Take Profit: {tp}

Condiciones:
- EMA20 < EMA50
- RSI < 50
- Vela roja de confirmación
"""

    return None


# =========================================================
# PARES A ANALIZAR
# =========================================================
pares = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "XAUUSD=X"]


# =========================================================
# MAIN LOOP (GitHub Actions)
# =========================================================
print("\n=== BOT EJECUTÁNDOSE (YFINANCE) ===\n")

for par in pares:
    df = get_data(par)
    time.sleep(2)  # pequeña pausa anti bloqueo

    if df is None:
        continue

    df = aplicar_indicadores(df)
    senal = generar_senal(df, par)

    if senal:
        enviar_correo(f"Señal detectada: {par}", senal)
        print(senal)
    else:
        print(f"Sin señal en {par}")

print("\n--- Fin de ejecución ---")
