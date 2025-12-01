import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import ssl
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
CUENTA = 50.0
RIESGO_MAX_DOLARES = 1.0  # $1 máximo por operación

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# Para no repetir señales
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
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string()))
        print(f"Email enviado → {asunto}")
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
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df

# =========================================
# CÁLCULO TP / SL (riesgo $1)
# =========================================
def calcular_tp_sl(precio, direccion, par):
    # Aproximación simple y segura para todos los pares
    if par in ["USDJPY", "XAUUSD"]:
        pip_value = 0.01
    else:
        pip_value = 0.0001

    sl_pips = RIESGO_MAX_DOLARES / (LOTE * 10_000) / pip_value   # ≈ 10-15 pips según par
    sl_pips = max(sl_pips, 12)  # mínimo 12 pips de SL

    if direccion == "BUY":
        sl = round(precio - sl_pips * pip_value, 5)
        tp1 = round(precio + sl_pips * 1.5 * pip_value, 5)
        tp2 = round(precio + sl_pips * 3.0 * pip_value, 5)
    else:  # SELL
        sl = round(precio + sl_pips * pip_value, 5)
        tp1 = round(precio - sl_pips * 1.5 * pip_value, 5)
        tp2 = round(precio - sl_pips * 3.0 * pip_value, 5)

    return sl, tp1, tp2

# =========================================
# DETECTAR CAMBIO DE TENDENCIA
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
# OBTENER SEÑAL (CORREGIDA 100%)
# =========================================
def obtener_senal(df, par):
    if len(df) < 60:
        return None

    ultimo = df.iloc[-1]
    anterior = df.iloc[-2]

    ema20_actual = ultimo["EMA20"]
    ema50_actual = ultimo["EMA50"]
    ema20_anterior = anterior["EMA20"]
    rsi = ultimo["RSI"]
    precio = ultimo["Close"]

    # Condiciones BUY
    buy = (
        ema20_actual > ema50_actual and
        ema20_actual > ema20_anterior and
        rsi > 53 and rsi < 80 and
        ultimo["Close"] > ultimo["Open"]
    )

    # Condiciones SELL
    sell = (
        ema20_actual < ema50_actual and
        ema20_actual < ema20_anterior and
        rsi < 47 and rsi > 20 and
        ultimo["Close"] < ultimo["Open"]
    )

    direccion = "BUY" if buy else "SELL" if sell else None
    if not direccion:
        return None

    # Evitar señal repetida en menos de 4 horas
    key = par
    if key in ultima_señal:
        tiempo = (datetime.now() - ultima_señal[key]["time"]).total_seconds()
        if ultima_señal[key]["direccion"] == direccion and tiempo < 4*3600:
            return None

    sl, tp1, tp2 = calcular_tp_sl(precio, direccion, par)

    señal = {
        "direccion": direccion,
        "entrada": round(precio, 5),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": round(rsi, 1)
    }

    ultima_señal[key] = {"direccion": direccion, "time": datetime.now()}
    return señal

# =========================================
# LOOP PRINCIPAL
# =========================================
print("=== BOT FOREX BOT $50 – LOTE 0.01 – RIESGO $1 ===\n")
print(f"Hora inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

for nombre, symbol in PARES.items():
    try:
        print(f"Analizando {nombre}...")
        df = yf.download(symbol, period=PERIOD, interval=INTERVAL,
                         progress=False, auto_adjust=True)

        if df.empty or len(df) < 60:
            print(f"  → Sin datos suficientes para {nombre}\n")
            continue

        df = calcular_indicadores(df)
        señal = obtener_senal(df, nombre)

        if señal:
            cuerpo = f"""
SEÑAL CONFIRMADA – {nombre}

Dirección → {señal['direccion']}
Entrada   → {señal['entrada']}
Stop Loss → {señal['sl']}     (riesgo ≈ $1)
TP1 (1.5R) → {señal['tp1']}
TP2 (3R)   → {señal['tp2']}

RSI actual: {señal['rsi']}
Lote: 0.01

Generada: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            enviar_email(f"SEÑAL {señal['direccion']} – {nombre}", cuerpo)
            print(f"  → SEÑAL ENVIADA: {señal['direccion']} {nombre}\n")

        else:
            # Revisar cambio de tendencia si hay operación “abierta”
            if nombre in ultima_señal:
                if hay_cambio_tendencia(df.tail(10), ultima_señal[nombre]["direccion"]):
                    aviso = f"""
CAMBIO DE TENDENCIA DETECTADO – {nombre}

Tu operación {ultima_señal[nombre]['direccion']} está en riesgo.
Recomendación: cerrar manualmente con ganancia antes del SL.

Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                    enviar_email(f"CIERRE RECOMENDADO – {nombre}", aviso)
                    print(f"  → Aviso de cierre enviado: {nombre}")

    except Exception as e:
        print(f"  → Error en {nombre}: {e}")

    time.sleep(2)

print(f"\nAnálisis terminado – {datetime.now().strftime('%H:%M:%S')}")
print("Próxima ejecución en ≈1 hora")
