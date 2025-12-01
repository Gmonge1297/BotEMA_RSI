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
RIESGO_MAX_DOLARES = 1.0  # Riesgo máximo $1 por operación

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# Para evitar spam: última señal por par
ultima_señal = {}

# =========================================
# FUNCIÓN ENVIAR EMAIL
# =========================================
def enviar_email(asunto, cuerpo):
    if not all([EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO]):
        print("⚠️ Credenciales de email faltantes")
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
        print(f"📧 Email enviado: {asunto}")
    except Exception as e:
        print(f"⚠️ Error enviando email: {e}")

# =========================================
# CÁLCULO INDICADORES
# =========================================
def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)  # Evitar division by zero
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(50)  # Rellenar NaN con 50 (neutro) - CORREGIDO SIN INPLACE

    return df

# =========================================
# CÁLCULO TP / SL
# =========================================
def calcular_tp_sl(precio_entrada, direccion, par):
    # Ajuste de pip_value por par (aprox para lote 0.01)
    if "JPY" in par:
        pip_value = 0.01  # Para pares como USDJPY
    elif "XAU" in par:
        pip_value = 0.1   # Para oro
    else:
        pip_value = 0.0001  # Para EURUSD, GBPUSD

    sl_pips = RIESGO_MAX_DOLARES / (LOTE * 10)  # Riesgo $1 → ≈10 pips estándar
    sl_pips = max(sl_pips, 12)  # SL mínimo 12 pips

    if direccion == "BUY":
        sl = round(precio_entrada - sl_pips * pip_value, 5)
        tp = round(precio_entrada + sl_pips * 2 * pip_value, 5)  # 2R
    else:
        sl = round(precio_entrada + sl_pips * pip_value, 5)
        tp = round(precio_entrada - sl_pips * 2 * pip_value, 5)

    return sl, tp

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
# OBTENER SEÑAL (últimas 3 velas, al menos 2 confirmatorias)
# =========================================
def obtener_senal(df, par):
    if len(df) < 60:
        return None

    ultimo = df.iloc[-1]
    ultimas_3 = df.iloc[-3:]

    ema20 = ultimo["EMA20"]
    ema50 = ultimo["EMA50"]
    rsi = ultimo["RSI"]
    precio = ultimo["Close"]

    alcistas = (ultimas_3["Close"] > ultimas_3["Open"]).sum()
    bajistas = (ultimas_3["Close"] < ultimas_3["Open"]).sum()

    direccion = None
    if ema20 > ema50 and rsi > 50 and rsi < 75 and alcistas >= 2:
        direccion = "BUY"
    elif ema20 < ema50 and rsi < 50 and rsi > 25 and bajistas >= 2:
        direccion = "SELL"

    if direccion is None:
        return None

    # Evitar señal repetida en menos de 4 horas
    if par in ultima_señal:
        if ultima_señal[par]["direccion"] == direccion and (datetime.now() - ultima_señal[par]["time"]).total_seconds() < 14400:
            return None

    sl, tp = calcular_tp_sl(precio, direccion, par)

    señal = {
        "direccion": direccion,
        "entrada": round(precio, 5),
        "sl": sl,
        "tp": tp,
        "rsi": round(rsi, 1)
    }

    ultima_señal[par] = {"direccion": direccion, "time": datetime.now()}
    return señal

# =========================================
# LOOP PRINCIPAL
# =========================================
print("=== BOT FOREX $50 - LOTE 0.01 - RIESGO $1 Iniciado")
print(f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

for nombre, symbol in PARES.items():
    try:
        print(f"Analizando {nombre}...")
        df = yf.download(symbol, period=PERIOD, interval=INTERVAL, progress=False)

        if df.empty or len(df) < 60:
            print(f"No hay datos suficientes para {nombre}\n")
            continue

        df = calcular_indicadores(df)
        señal = obtener_senal(df, nombre)

        if señal:
            cuerpo = f"""
Señal confirmada — {nombre} ({señal['direccion']})

Entrada: {señal['entrada']}
Stop Loss: {señal['sl']}
Take Profit: {señal['tp']}
RSI: {señal['rsi']}
Lote sugerido: 0.01
Riesgo por trade (USD aprox): 1.00
Bot: EMA20/EMA50 + RSI (flex) + 2 de 3 velas

Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            enviar_email(f"Señal {señal['direccion']} — {nombre}", cuerpo)
            print(f"SEÑAL ENVIADA: {señal['direccion']} {nombre}\n")
        else:
            # Revisar cambio de tendencia si hay señal anterior
            if nombre in ultima_señal:
                if hay_cambio_tendencia(df, ultima_señal[nombre]["direccion"]):
                    aviso = f"""
CAMBIO DE TENDENCIA DETECTADO EN {nombre}

Tu operación {ultima_señal[nombre]["direccion"]} puede estar en peligro.
Recomendación: CIERRA MANUALMENTE CON GANANCIA antes de que toque el SL.

Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                    enviar_email(f"CIERRE RECOMENDADO - {nombre}", aviso)
                    print(f"Aviso de cierre enviado para {nombre}\n")

    except Exception as e:
        print(f"Error en {nombre}: {e}")

    time.sleep(2)  # Pausa para no saturar yfinance

print("Análisis completado. Próxima ejecución en 1 hora.")
