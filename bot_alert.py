import pandas as pd
import numpy as np
import datetime as dt
import pytz
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yfinance as yf
import os


# ===============================
# CONFIGURACIÓN
# ===============================

CR_TZ = pytz.timezone("America/Costa_Rica")

EMAIL_USER = os.getenv("EMAIL_USER")      # Gmail del bot
EMAIL_PASS = os.getenv("EMAIL_PASSWORD")  # App Password
EMAIL_TO   = os.getenv("EMAIL_TO")        # Gmail donde recibirás señales

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

SL_PIPS = 300
TP_PIPS = 600
MAX_RISK_USD = 1.5


# Pares y sus tickers en Yahoo
PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}


# ===============================
# INDICADORES
# ===============================

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    avg_gain = pd.Series(gain).rolling(window=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period).mean()
    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ===============================
# EMAIL HTML
# ===============================

def send_email(subject, html_body):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject

        msg.attach(MIMEText(html_body, "html"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()

        print("Correo enviado:", subject)
    except Exception as e:
        print("Error enviando correo:", e)


# ===============================
# VALOR DE PIP Y LOTE
# ===============================

def pip_value(symbol):
    if "JPY" in symbol:
        return 0.01
    if "XAU" in symbol:
        return 0.1
    return 0.0001


def calculate_lot(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)
    if sl_pips == 0:
        return 0.01

    value_per_pip_0_01 = 0.10
    lot = max_risk / (sl_pips * value_per_pip_0_01)

    return max(round(lot, 2), 0.01)


# ===============================
# GENERAR SEÑAL
# ===============================

def generar_senal(df, symbol):
    if df is None or df.empty or len(df) < 60:
        print("Sin datos suficientes.\n")
        return None

    # Normalizar columnas a minúscula
    df.columns = df.columns.str.lower()

    close = df["close"]
    openv = df["open"]

    df["ema20"] = ema(close, EMA_FAST)
    df["ema50"] = ema(close, EMA_SLOW)
    df["rsi"] = rsi(close, RSI_PERIOD)

    # Velas
    p = df.iloc[-2]   # vela previa
    c = df.iloc[-1]   # última vela

    # Cruces
    cruce_up = p["ema20"] <= p["ema50"] and c["ema20"] > c["ema50"]
    cruce_dn = p["ema20"] >= p["ema50"] and c["ema20"] < c["ema50"]

    # Confirmación de compra
    buy_ok = (
        cruce_up and
        c["close"] > c["open"] and
        c["rsi"] > RSI_BUY
    )

    # Confirmación de venta
    sell_ok = (
        cruce_dn and
        c["close"] < c["open"] and
        c["rsi"] < RSI_SELL
    )

    if buy_ok:
        entry = float(c["close"])
        sl = entry - SL_PIPS * pip_value(symbol)
        tp = entry + TP_PIPS * pip_value(symbol)
        lot = calculate_lot(entry, sl, MAX_RISK_USD, symbol)

        return {
            "tipo": "BUY",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rsi": float(c["rsi"]),
            "lot": lot
        }

    if sell_ok:
        entry = float(c["close"])
        sl = entry + SL_PIPS * pip_value(symbol)
        tp = entry - TP_PIPS * pip_value(symbol)
        lot = calculate_lot(entry, sl, MAX_RISK_USD, symbol)

        return {
            "tipo": "SELL",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rsi": float(c["rsi"]),
            "lot": lot
        }

    return None


# ===============================
# LOOP PRINCIPAL
# ===============================

if __name__ == "__main__":
    print("=== BOT EJECUTANDO (YFINANCE + TP/SL + HTML) ===\n")

    for par, ticker in PAIRS.items():
        print(f"\nDescargando datos de {ticker}...")
        df = yf.download(ticker, interval="1h", period="7d")
        
        # Normalizar columnas apenas se descargan
        df.columns = df.columns.str.lower()

        senal = generar_senal(df, par)

        if senal:
            html = f"""
            <h2>📌 Señal {senal['tipo']} Confirmada - {par}</h2>
            <p><b>Entrada:</b> {senal['entry']}</p>
            <p><b>Stop Loss:</b> {senal['sl']}</p>
            <p><b>Take Profit:</b> {senal['tp']}</p>
            <p><b>RSI:</b> {senal['rsi']}</p>
            <p><b>Lote sugerido:</b> {senal['lot']}</p>
            <p><b>Riesgo fijo:</b> ${MAX_RISK_USD}</p>
            """

            send_email(f"{senal['tipo']} Confirmado - {par}", html)

        time.sleep(1)

    print("\nNo hubo señales esta hora.\n")
