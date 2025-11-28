import yfinance as yf
import pandas as pd
import numpy as np
import requests
import json
from datetime import datetime

# =======================
# CONFIGURACIÓN
# =======================

TELEGRAM_TOKEN = "AQUI_TU_TOKEN"
TELEGRAM_CHAT_ID = "AQUI_TU_CHAT_ID"

SIMBOLOS = {
    "EURUSD": "EURUSD=X",
}

INTERVAL = "5m"
PERIOD = "1d"
LAST_TRADE_FILE = "last_trade.json"


# =======================
# FUNCIONES UTILITARIAS
# =======================

def enviar_alerta(msg: str):
    """ Envía mensaje a Telegram """
    print("\n===== ALERTA ENVIADA =====")
    print(msg)
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            params={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
    except:
        pass


def cargar_ultimo_trade():
    try:
        with open(LAST_TRADE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def guardar_ultimo_trade(data):
    with open(LAST_TRADE_FILE, "w") as f:
        json.dump(data, f)


# =======================
# Cálculo de indicadores
# =======================

def calcular_rsi(df, period=14):
    """ Cálculo estandar del RSI, corregido para evitar arrays 2D """

    delta = df["Close"].diff()

    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    df["RSI"] = rsi
    return df


def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df = calcular_rsi(df)
    return df


# =======================
# Lógica de señales
# =======================

def detectar_senal(df):
    df = df.dropna()
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Condiciones BUY agresiva
    if (
        prev["EMA20"] < prev["EMA50"] and
        last["EMA20"] > last["EMA50"] and
        last["RSI"] > 50
    ):
        return "BUY"

    # Condiciones SELL agresiva
    if (
        prev["EMA20"] > prev["EMA50"] and
        last["EMA20"] < last["EMA50"] and
        last["RSI"] < 50
    ):
        return "SELL"

    return None


# =======================
# Procesamiento por símbolo
# =======================

def procesar_simbolo(nombre, yf_symbol):
    print(f"\n[ {datetime.now()} ] Descargando datos de {nombre} ({yf_symbol})...")

    df = yf.download(yf_symbol, interval=INTERVAL, period=PERIOD, progress=False)

    if df is None or df.empty:
        print("❌ No se pudieron descargar datos")
        return

    df = calcular_indicadores(df)

    senal = detectar_senal(df)
    if not senal:
        print(f"{nombre}: Sin señal.")
        return

    ultimo_trade = cargar_ultimo_trade()
    ultimo = ultimo_trade.get(nombre, "NONE")

    # Evitar repetir misma señal
    if senal == ultimo:
        print(f"{nombre}: señal {senal} ignorada (misma que la anterior).")
        return

    # Guardar nueva señal
    ultimo_trade[nombre] = senal
    guardar_ultimo_trade(ultimo_trade)

    # Datos finales
    precio = round(df.iloc[-1]["Close"], 5)

    # Calcular SL / TP agresivo
    if senal == "BUY":
        sl = round(precio - 0.0020, 5)
        tp = round(precio + 0.0040, 5)
    else:
        sl = round(precio + 0.0020, 5)
        tp = round(precio - 0.0040, 5)

    mensaje = (
        f"🔥 *SEÑAL DETECTADA ({nombre})*\n\n"
        f"Tipo: *{senal}*\n"
        f"Precio: {precio}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        f"Timeframe: {INTERVAL}\n\n"
        f"EMA20/EMA50 + RSI"
    )

    enviar_alerta(mensaje)


# =======================
# MAIN
# =======================

if __name__ == "__main__":
    print("=== Bot EMA+RSI Agresivo ===\n")
    
    for nombre, yf_symbol in SIMBOLOS.items():
        procesar_simbolo(nombre, yf_symbol)

    print("\n=== Terminado ===")
