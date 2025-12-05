#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import smtplib
from email.mime.text import MIMEText

# ========================================
# CONFIGURACIÓN GENERAL
# ========================================

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F",
}

INTERVAL = "15m"
PERIOD = "7d"

RISK_DOLLARS = 1.50
MAX_SPREAD = 0.0008
MIN_RSI_BUY = 65
MAX_RSI_SELL = 35
EMA_FAST = 20
EMA_SLOW = 50

EMAIL_FROM = "BOT"
EMAIL_TO = "TU_CORREO"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "TU_CORREO"
SMTP_PASS = "TU_PASSWORD"

# ========================================
# FUNCIÓN PARA ENVIAR ALERTAS
# ========================================

def enviar_alerta(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        server.quit()

        print("[✔] Correo enviado.")

    except Exception as e:
        print("[ERROR enviando correo]:", str(e))


# ========================================
# DESCARGA ROBUSTA COMPATIBLE 2025
# ========================================

def descargar_datos(symbol):
    try:
        df = yf.download(
            symbol,
            period=PERIOD,
            interval=INTERVAL,
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if df is None or len(df) < 60:
            return None

        # Si Yahoo envía MultiIndex (2025)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ['_'.join(col).lower() for col in df.columns.values]

        # Normalizar nombres
        rename_map = {}
        for col in df.columns:
            lc = col.lower()
            if "open" in lc: rename_map[col] = "open"
            elif "high" in lc: rename_map[col] = "high"
            elif "low" in lc: rename_map[col] = "low"
            elif "close" in lc: rename_map[col] = "close"
            elif "volume" in lc: rename_map[col] = "volume"

        df = df.rename(columns=rename_map)

        for req in ["open", "high", "low", "close"]:
            if req not in df.columns:
                return None

        df = df.dropna().copy()

        # EMA
        df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

        # RSI
        delta = df["close"].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(14).mean()
        avg_loss = pd.Series(loss).rolling(14).mean()
        rs = avg_gain / avg_loss
        df["rsi"] = 100 - (100 / (1 + rs))

        df = df.dropna().copy()

        return df

    except Exception as e:
        print("[ERROR descarga]:", str(e))
        return None


# ========================================
# CÁLCULO DE LOTE
# ========================================

def calcular_lote(entry, stop, risk):
    risk_pips = abs(entry - stop)
    if risk_pips == 0:
        return 0.01
    lot = risk / risk_pips
    return max(0.01, round(lot, 2))


# ========================================
# ESTRATEGIA PRO
# ========================================

def analizar_par(name, symbol):
    print(f"[{datetime.datetime.now()}] Analizando {name} ({symbol})")

    df = descargar_datos(symbol)
    if df is None:
        print("  — Datos insuficientes.")
        return

    last = df.iloc[-1]
    spread = last["high"] - last["low"]

    if spread > MAX_SPREAD:
        print("  — Spread muy alto → sin señal")
        return

    ema_bull = last["ema_fast"] > last["ema_slow"]
    ema_bear = last["ema_fast"] < last["ema_slow"]

    rsi = last["rsi"]
    price = last["close"]

    # --------------------------
    # BUY PRO
    # --------------------------
    if ema_bull and rsi >= MIN_RSI_BUY:
        entry = price
        sl = price - (spread * 2)
        tp = price + (spread * 4)
        lot = calcular_lote(entry, sl, RISK_DOLLARS)

        cuerpo = f"""
📈 BUY Confirmado {name}

Entrada: {entry}
SL: {sl}
TP: {tp}
RSI: {round(rsi,2)}
Riesgo: ${RISK_DOLLARS}
Lote sugerido: {lot}
"""
        enviar_alerta(f"BUY {name}", cuerpo)
        print(cuerpo)
        return

    # --------------------------
    # SELL PRO
    # --------------------------
    if ema_bear and rsi <= MAX_RSI_SELL:
        entry = price
        sl = price + (spread * 2)
        tp = price - (spread * 4)
        lot = calcular_lote(entry, sl, RISK_DOLLARS)

        cuerpo = f"""
📉 SELL Confirmado {name}

Entrada: {entry}
SL: {sl}
TP: {tp}
RSI: {round(rsi,2)}
Riesgo: ${RISK_DOLLARS}
Lote sugerido: {lot}
"""
        enviar_alerta(f"SELL {name}", cuerpo)
        print(cuerpo)
        return

    print("  — Sin señal PRO.")


# ========================================
# CICLO PRINCIPAL
# ========================================

print(f"[{datetime.datetime.now()}] === Bot PRO ejecutándose (modo CRON) ===")

for name, symbol in PAIRS.items():
    analizar_par(name, symbol)

print(f"[{datetime.datetime.now()}] === Fin del ciclo PRO ===")
