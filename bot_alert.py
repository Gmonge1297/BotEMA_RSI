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
    ",
    "XAUUSD": "GC=F"           # Oro (Gold futures)
}

PERIOD = "60d"
INTERVAL = "1h"
LOTE = 0.01
RIESGO_MAX_DOLARES = 1.0

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# Control de señales repetidas (máximo 1 cada 4 horas por par)
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
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        print(f"Email enviado → {asunto}")
    except Exception as e:
        print(f"Error enviando email: {e}")

# =========================================
# INDICADORES (corregido y optimizado)
# =========================================
def calcular_indicadores(df):
    if df.empty:
        return df

    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(window=14).mean()
    loss = -delta.clip(upper=0).rolling(window=14).mean()

    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(50)

    return df

# =========================================
# TP / SL con riesgo fijo de ~$1
# =========================================
def calcular_tp_sl(precio_entrada, direccion, par):
    if "JPY" in par:
        pip_value = 0.01
    elif "XAU" in par:
        pip_value = 0.10
    else:
        pip_value = 0.0001

    # Con lote 0.01 → ~10 USD por pip en pares normales → queremos arriesgar solo $1 → ~10 pips
    sl_pips = max(12, round(RIESGO_MAX_DOLARES / (LOTE * 100000 * pip_value), 1))

    if direccion == "BUY":
        sl = round(precio_entrada - sl_pips * pip_value, 5 if "JPY" not in par else 3)
        tp = round(precio_entrada + sl_pips * 2 * pip_value, 5 if "JPY" not in par else 3)
    else:
        sl = round(precio_entrada + sl_pips * pip_value, 5 if "JPY" not in par else 3)
        tp = round(precio_entrada - sl_pips * 2 * pip_value, 5 if "JPY" not in par else 3)

    return sl, tp

# =========================================
# DETECTAR CAMBIO DE TENDENCIA (para aviso de cierre)
# =========================================
def hay_cambio_tendencia(df, direccion_actual):
    ema20_actual = df["EMA20"].iloc[-1]
    ema50_actual = df["EMA50"].iloc[-1]

    if direccion_actual == "BUY" and ema20_actual < ema50_actual:
        return True
    if direccion_actual == "SELL" and ema20_actual > ema50_actual:
        return True
    return False

# =========================================
# GENERAR SEÑAL (todo con .iloc[-1], nunca Series en if)
# =========================================
def obtener_senal(df, par):
    if len(df) < 60:
        return None

    # Últimas 3 velas cerradas
    ultimas_3 = df.iloc[-3:]

    close = df["Close"].iloc[-1]
    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]
    rsi   = df["RSI"].iloc[-1]

    velas_alcistas = (ultimas_3["Close"] > ultimas_3["Open"]).sum()
    velas_bajistas = (ultimas_3["Close"] < ultimas_3["Open"]).sum()

    direccion = None

    # Condiciones de COMPRA
    if (ema20 > ema50 and
        50 < rsi < 75 and
        velas_alcistas >= 2):
        direccion = "BUY"

    # Condiciones de VENTA
    elif (ema20 < ema50 and
          25 < rsi < 50 and
          velas_bajistas >= 2):
        direccion = "SELL"

    if direccion is None:
        return None

    # Evitar señal repetida en menos de 4 horas
    if par in ultima_señal:
        tiempo_desde_ultima = (datetime.now() - ultima_señal[par]["time"]).total_seconds()
        if ultima_señal[par]["direccion"] == direccion and tiempo_desde_ultima < 14400:  # 4 horas
            return None

    sl, tp = calcular_tp_sl(close, direccion, par)

    señal = {
        "direccion": direccion,
        "entrada": round(close, 5),
        "sl": sl,
        "tp": tp,
        "rsi": round(rsi, 1)
    }

    ultima_señal[par] = {"direccion": direccion, "time": datetime.now()}
    return señal

# =========================================
# BUCLE PRINCIPAL (con sleep de 1 hora al final)
# =========================================
print("=== BOT FOREX $50 - LOTE 0.01 - RIESGO $1 ===")
print(f"Iniciado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

while True:
    for nombre, ticker in PARES.items():
        try:
            print(f"Analizando {nombre}...", end=" ")

            # yfinance a veces devuelve MultiIndex, lo aplanamos
            df = yf.download(ticker, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=True)

            if df.empty or len(df) < 60:
                print("Sin datos suficientes")
                continue

            # Corregir posible MultiIndex de columnas
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)

            df = calcular_indicadores(df.copy())
            señal = obtener_senal(df, nombre)

            if señal:
                cuerpo = f"""
SEÑAL {señal['direccion']} — {nombre}

Entrada: {señal['entrada']}
Stop Loss:  {señal['sl']}
Take Profit: {señal['tp']}
RSI actual: {señal['rsi']}
Lote: {LOTE}
Riesgo ≈ $1.00 | Reward 2:1

Generado el {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Estrategia: EMA20 vs EMA50 + RSI + 2/3 velas confirmatorias
"""
                enviar_email(f"SEÑAL {señal['direccion']} → {nombre}", cuerpo)
                print("¡SEÑAL ENVIADA!")

            else:
                print("sin señal")
                # Aviso de posible cambio de tendencia
                if nombre in ultima_señal:
                    if hay_cambio_tendencia(df, ultima_señal[nombre]["direccion"]):
                        aviso = f"""
CAMBIO DE TENDENCIA DETECTADO EN {nombre}

Tu posición {ultima_señal[nombre]['direccion']} está en riesgo.
Recomendación: cerrar manualmente antes del SL.

Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
                        enviar_email(f"CIERRE RECOMENDADO – {nombre}", aviso)
                        print("Aviso de cierre enviado")

        except Exception as e:
            print(f"Error: {e}")

        time.sleep(3)  # ser amables con yfinance

    print(f"\nAnálisis completado – {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Próxima ejecución en 1 hora...\n" + "-"*60)
    time.sleep(3600)  # Espera 1 hora exacta
