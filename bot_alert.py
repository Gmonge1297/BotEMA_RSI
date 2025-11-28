import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
import smtplib
from email.mime.text import MIMEText

# ============================
# CONFIGURACIÓN GENERAL
# ============================

INTERVAL = "1h"
PERIOD = "30d"

ARCHIVO_TRADE = "last_trade.json"

SIMBOLOS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO   = os.getenv("EMAIL_TO")


# ============================
# CARGAR / GUARDAR TRADE
# ============================

def cargar_trade():
    if not os.path.exists(ARCHIVO_TRADE):
        return None

    try:
        with open(ARCHIVO_TRADE, "r") as f:
            return json.load(f)
    except:
        return None


def guardar_trade(data):
    with open(ARCHIVO_TRADE, "w") as f:
        json.dump(data, f, indent=4)


def limpiar_trade():
    if os.path.exists(ARCHIVO_TRADE):
        os.remove(ARCHIVO_TRADE)


# ============================
# INDICADORES
# ============================

def calcular_RSI(df, period=14):
    delta = df["Close"].diff()

    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)

    # FIX: asegurar que son 1D
    up = up.values.flatten()
    down = down.values.flatten()

    roll_up = pd.Series(up).rolling(period).mean()
    roll_down = pd.Series(down).rolling(period).mean()

    RS = roll_up / roll_down
    rsi = 100 - (100 / (1 + RS))

    df["RSI"] = rsi
    return df


def calcular_EMA(df, period, column="Close"):
    return df[column].ewm(span=period, adjust=False).mean()


def calcular_indicadores(df):
    df["EMA20"] = calcular_EMA(df, 20)
    df["EMA50"] = calcular_EMA(df, 50)
    df = calcular_RSI(df)
    return df.dropna()


# ============================
# DETECCIÓN DE SEÑALES (ENTRADA)
# ============================

def detectar_senal(df):

    prev = df.iloc[-2]
    last = df.iloc[-1]

    # BUY
    if (
        prev["EMA20"] < prev["EMA50"]
        and last["EMA20"] > last["EMA50"]
        and last["RSI"] > 51
        and last["Close"] > last["EMA20"]
    ):
        return ("BUY", last["Close"])

    # SELL
    if (
        prev["EMA20"] > prev["EMA50"]
        and last["EMA20"] < last["EMA50"]
        and last["RSI"] < 49
        and last["Close"] < last["EMA20"]
    ):
        return ("SELL", last["Close"])

    return None


# ============================
# DETECCIÓN DE SEÑALES DE SALIDA (AGRESIVO)
# ============================

def evaluar_salida_agresiva(df, trade):
    direction = trade["type"]
    entry = trade["entry_price"]
    current = df.iloc[-1]

    profit = current["Close"] - entry if direction == "BUY" else entry - current["Close"]

    # 1. Siempre exige profit mínimo
    if profit <= 0:
        return False

    # 2. Señal de reversa fuerte
    ema20 = df["EMA20"]
    ema50 = df["EMA50"]

    rsi = current["RSI"]

    cond = 0

    # Condición 1: EMA20 perdiendo fuerza
    if abs(ema20.iloc[-1] - ema20.iloc[-2]) < 0.1:
        cond += 1

    # Condición 2: RSI indicando giro
    if direction == "BUY" and rsi < 50:
        cond += 1
    if direction == "SELL" and rsi > 50:
        cond += 1

    # Condición 3: vela contra tendencia
    if direction == "BUY" and current["Close"] < ema20.iloc[-1]:
        cond += 1
    if direction == "SELL" and current["Close"] > ema20.iloc[-1]:
        cond += 1

    # Si al menos 2 condiciones se cumplen → cerrar
    return cond >= 2


# ============================
# ENVÍO DE CORREO
# ============================

def enviar_email(asunto, mensaje):
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return

    msg = MIMEText(mensaje)
    msg["Subject"] = asunto
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()
        print("📧 Email enviado")
    except Exception as e:
        print("❌ Error enviando correo:", e)


# ============================
# PROCESAR SÍMBOLO
# ============================

def procesar_simbolo(nombre, yf_symbol):
    print(f"\n[ {datetime.utcnow()} ] Descargando datos de {nombre} ({yf_symbol})...")

    df = yf.download(yf_symbol, interval=INTERVAL, period=PERIOD, progress=False)
    if df is None or df.empty:
        print("⚠️ No se pudieron obtener datos.")
        return

    df = calcular_indicadores(df)
    if df.empty:
        print("⚠️ No hay suficientes velas.")
        return

    last = df.iloc[-1]

    # Cargar trade actual si existe
    trade = cargar_trade()

    # ======================
    # 1. Si hay trade abierto → evaluar salida
    # ======================
    if trade and trade["symbol"] == nombre:

        if evaluar_salida_agresiva(df, trade):
            mensaje = (
                f"⚠️ Señal de salida — {nombre}\n"
                f"Tipo: {trade['type']}\n"
                f"Entrada: {trade['entry_price']}\n"
                f"Precio actual: {last['Close']}\n"
                f"RSI: {last['RSI']:.1f}\n"
                f"EMA20: {last['EMA20']:.4f}\n"
                f"EMA50: {last['EMA50']:.4f}\n\n"
                f"El bot recomienda CERRAR esta operación."
            )

            enviar_email(f"Salida — {nombre}", mensaje)
            limpiar_trade()
            print(">> Señal de salida enviada.")
        else:
            print("— Trade abierto, sin salida.")
        return

    # ======================
    # 2. Buscar nueva señal
    # ======================
    senal = detectar_senal(df)

    if senal:
        tipo, entrada = senal

        SL = entrada - 30 if tipo == "BUY" else entrada + 30
        TP = entrada + 60 if tipo == "BUY" else entrada - 60

        trade_data = {
            "symbol": nombre,
            "type": tipo,
            "entry_price": float(entrada),
            "SL": float(SL),
            "TP": float(TP)
        }

        guardar_trade(trade_data)

        mensaje = (
            f"Señal confirmada — {nombre} ({tipo})\n"
            f"Entrada: {entrada}\n"
            f"Stop Loss: {SL}\n"
            f"Take Profit: {TP}\n"
            f"RSI: {last['RSI']:.1f}\n\n"
            f"Bot: EMA20/EMA50 + RSI + Vela\n"
            f"Generado: {datetime.utcnow()}"
        )

        enviar_email(f"Señal — {nombre}", mensaje)
        print(">> Señal encontrada y guardada.")
    else:
        print("— No hubo señal.")


# ============================
# EJECUCIÓN PRINCIPAL
# ============================

print("=== Bot EMA+RSI Agresivo ===")

for nombre, symbol in SIMBOLOS.items():
    procesar_simbolo(nombre, symbol)

print("\n=== Fin ejecución ===")
