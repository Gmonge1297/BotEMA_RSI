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
LOTE = 0.01
CUENTA = 50.0
RIESGO_MAX_DOLARES = 1.0  # Máximo $1 por operación

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# Para evitar spam: guardamos última señal por par
ultima_señal = {}

# =========================================
# ENVIAR EMAIL
# =========================================
def enviar_email(asunto, cuerpo):
    if not all([EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO]):
        print("Credenciales de email faltantes")
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
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

# =========================================
# CÁLCULO TP / SL
# =========================================
def calcular_tp_sl(precio_entrada, tipo_operacion, atr):
    riesgo_pips = RIESGO_MAX_DOLARES / (LOTE * 10000)  # para pares normales (no JPY ni oro)

    if "JPY" in tipo_operacion or tipo_operacion == "XAUUSD":
        riesgo_pips = RIESGO_MAX_DOLARES / (LOTE * 100)   # ajuste para JPY y oro

    if tipo_operacion == "USDJPY":
        riesgo_pips /= 100  # porque 1 pip = 0.01 en JPY

    sl_pips = max(riesgo_pips, atr * 1.0)  # SL mínimo = 1x ATR

    if tipo_operacion == "BUY":
        sl = round(precio_entrada - sl_pips * 0.0001, 5)
        tp1 = round(precio_entrada + sl_pips * 1.5 * 0.0001, 5)
        tp2 = round(precio_entrada + sl_pips * 3.0 * 0.0001, 5)
    else:  # SELL
        sl = round(precio_entrada + sl_pips * 0.0001, 5)
        tp1 = round(precio_entrada - sl_pips * 1.5 * 0.0001, 5)
        tp2 = round(precio_entrada - sl_pips * 3.0 * 0.0001, 5)

    return sl, tp1, tp2

# =========================================
# DETECTAR CAMBIO DE TENDENCIA (para cerrar con ganancia)
# =========================================
def hay_cambio_tendencia(df, direccion_actual):
    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]

    if direccion_actual == "BUY" and ema20 < ema50:
        return True
    if direccion_actual == "SELL" and ema20 > ema50:
        return True
    return False

# =========================================
# OBTENER SEÑAL
# =========================================
def obtener_senal(df, par):
    if len(df) < 60:
        return None

    ultimo = df.iloc[-1]
    anterior = df.iloc[-2]

    ema20_actual = ultimo["EMA20"]
    ema50_actual = ultimo["EMA50"]
    rsi = ultimo["RSI"]

    precio = ultimo["Close"]
    atr = df["Close"].tail(14).std() * 1.5  # ATR aproximado

    # Condiciones más estrictas y confiables
    buy_signal = (
        ema20_actual > ema50_actual and
        ema20_actual > ema20_actual.shift(1).iloc[-1] and
        rsi > 53 and rsi < 80 and
        ultimo["Close"] > ultimo["Open"]
    )

    sell_signal = (
        ema20_actual < ema50_actual and
        ema20_actual < ema20_actual.shift(1).iloc[-1] and
        rsi < 47 and rsi > 20 and
        ultimo["Close"] < ultimo["Open"]
    )

    direccion = None
    if buy_signal:
        direccion = "BUY"
    elif sell_signal:
        direccion = "SELL"

    if direccion is None:
        return None

    # Evitar señal repetida en el mismo sentido
    key = par
    if key in ultima_señal:
        if ultima_señal[key]["direccion"] == direccion and (datetime.now() - ultima_señal[key]["time"]).seconds < 4*3600:
            return None

    sl, tp1, tp2 = calcular_tp_sl(precio, direccion, atr)

    señal = {
        "direccion": direccion,
        "entrada": round(precio, 5),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": round(rsi, 1),
        "atr": round(atr, 5)
    }

    ultima_señal[key] = {"direccion": direccion, "time": datetime.now()}
    return señal

# =========================================
# LOOP PRINCIPAL
# =========================================
print("=== BOT FOREX $50 - LOTE 0.01 - RIESGO $1  Iniciado")
print(f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

for nombre, symbol in PARES.items():
    try:
        print(f"Analizando {nombre}...")
        df = yf.download(symbol, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)

        if df.empty or len(df) < 60:
            print(f"No hay datos suficientes para {nombre}\n")
            continue

        df = calcular_indicadores(df)
        señal = obtener_senal(df, nombre)

        if señal:
            cuerpo = f"""
SEÑAL CONFIRMADA - {nombre}

Dirección: {señal['direccion']}
Entrada aproximada: {señal['entrada']}
Stop Loss: {señal['sl']}  (Riesgo ≈ $1)
Take Profit 1: {señal['tp1']}  (+1.5R)
Take Profit 2: {señal['tp2']}  (+3R)

RSI actual: {señal['rsi']}
Lote recomendado: 0.01

Bot ejecutado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

            enviar_email(f"SEÑAL {señal['direccion']} — {nombre}", cuerpo)
            print(f"SEÑAL ENVIADA: {señal['direccion']} {nombre}\n")
        else:
            # Revisar si hay operación abierta y cambió la tendencia
            if nombre in ultima_señal:
                df_tail = df.tail(10)
                if hay_cambio_tendencia(df_tail, ultima_señal[nombre]["direccion"]):
                    aviso = f"""
CAMBIO DE TENDENCIA DETECTADO EN {nombre}

Tu operación {ultima_señal[nombre]["direccion"]} puede estar en peligro.
Recomendación: CIERRA MANUALMENTE CON GANANCIA antes de que toque el SL.

Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                    enviar_email(f"CIERRE RECOMENDADO - {nombre}", aviso)
                    print(f"Aviso de cierre enviado para {nombre}")

    except Exception as e:
        print(f"Error en {nombre}: {e}")

    time.sleep(2)  # ser amable con yfinance

print("Análisis completado. Próxima ejecución en 1 hora.")
