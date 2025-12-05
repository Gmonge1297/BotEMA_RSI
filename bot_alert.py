#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import os

# ==============================================
# CONFIGURACIÓN
# ==============================================

EMAIL_FROM = os.getenv("EMAIL_USER")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASS = os.getenv("EMAIL_PASSWORD")

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F",
}

PERIOD = "1d"
WINDOW_FAST = 50
WINDOW_SLOW = 200

# ==============================================
# FUNCIÓN PARA ENVIAR CORREO
# ==============================================

def enviar_correo(asunto, mensaje):
    msg = MIMEText(mensaje)
    msg["Subject"] = asunto
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        server.quit()
        print("[EMAIL] Enviado correctamente.")
    except Exception as e:
        print("[EMAIL ERROR]:", e)

# ==============================================
# PROCESO DE CARGA Y CÁLCULO DE INDICADORES
# ==============================================

def obtener_datos(ticker):
    try:
        df = yf.download(ticker, period=PERIOD, interval="1h", progress=False)

        if df is None or df.empty:
            return None

        # Manejo del MultiIndex (Yahoo a veces lo devuelve así)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Solo dejamos las columnas necesarias
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()

        df.dropna(inplace=True)
        return df

    except Exception as e:
        print(f"[ERROR descarga]: {e}\n  — Datos insuficientes.\n")
        return None

# ==============================================
# GENERAR SEÑAL
# ==============================================

def generar_senal(df):

    df["ema_fast"] = df["Close"].ewm(span=WINDOW_FAST, adjust=False).mean()
    df["ema_slow"] = df["Close"].ewm(span=WINDOW_SLOW, adjust=False).mean()

    df.dropna(inplace=True)
    if df.empty:
        return None

    c = df["Close"].iloc[-1]
    ema_fast = df["ema_fast"].iloc[-1]
    ema_prev_fast = df["ema_fast"].iloc[-2]
    ema_slow = df["ema_slow"].iloc[-1]
    ema_prev_slow = df["ema_slow"].iloc[-2]

    # Cruce alcista
    if ema_prev_fast < ema_prev_slow and ema_fast > ema_slow:
        tipo = "COMPRA"
    # Cruce bajista
    elif ema_prev_fast > ema_prev_slow and ema_fast < ema_slow:
        tipo = "VENTA"
    else:
        return None

    # StopLoss y TakeProfit
    if tipo == "COMPRA":
        sl = c - (df["ATR"].iloc[-1] * 1.5)
        tp = c + (df["ATR"].iloc[-1] * 2)
    else:
        sl = c + (df["ATR"].iloc[-1] * 1.5)
        tp = c - (df["ATR"].iloc[-1] * 2)

    return {
        "tipo": tipo,
        "precio": c,
        "sl": sl,
        "tp": tp
    }

# ==============================================
# CÁLCULO DE ATR
# ==============================================

def agregar_atr(df, periodo=14):
    df["H-L"] = df["High"] - df["Low"]
    df["H-C"] = abs(df["High"] - df["Close"].shift(1))
    df["L-C"] = abs(df["Low"] - df["Close"].shift(1))
    df["TR"] = df[["H-L", "H-C", "L-C"]].max(axis=1)
    df["ATR"] = df["TR"].rolling(periodo).mean()
    df.dropna(inplace=True)
    return df

# ==============================================
# FORMATO CON 5 DECIMALES
# ==============================================

def f(x):
    return f"{x:.5f}"

# ==============================================
# MAIN
# ==============================================

def main():
    print(f"[{datetime.utcnow()}] === Bot PRO ejecutándose (modo CRON) ===\n")

    mensaje_final = ""

    for nombre, ticker in SYMBOLS.items():
        print(f"Analizando {nombre} ({ticker})")

        df = obtener_datos(ticker)
        if df is None or df.empty:
            mensaje_final += f"{nombre}: ❌ No hay datos.\n"
            continue

        df = agregar_atr(df)

        senal = generar_senal(df)
        if senal is None:
            mensaje_final += f"{nombre}: — Sin señal por ahora.\n"
            continue

        mensaje_final += (
            f"\n⚡ Señal en {nombre}\n"
            f"Tipo: {senal['tipo']}\n"
            f"Entrada: {f(senal['precio'])}\n"
            f"Stop Loss: {f(senal['sl'])}\n"
            f"Take Profit: {f(senal['tp'])}\n"
        )

    print("\n=== Fin del ciclo PRO ===\n")

    enviar_correo("📈 Señales Forex – Bot PRO", mensaje_final if mensaje_final else "Sin señales hoy.")

# ==============================================
# EJECUCIÓN
# ==============================================

if __name__ == "__main__":
    main()
