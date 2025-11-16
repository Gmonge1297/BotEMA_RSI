import pandas as pd
import numpy as np
import yfinance as yf
import datetime as dt
import pytz
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==============================
#    CONFIGURACIÓN GENERAL
# ==============================

CR_TZ = pytz.timezone("America/Costa_Rica")

EMA_FAST = 20
EMA_SLOW = 50

RSI_PERIOD = 14
RSI_BUY = 55
RSI_SELL = 45

SL_PIPS = 300
TP_PIPS = 600

TIMEFRAME_MINUTES = 60

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

# ==============================
#    EMAIL CONFIG
# ==============================

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")  # tu correo donde llegan señales


def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print("Correo enviado ✔")
    except Exception as e:
        print("Error enviando correo:", e)


# ==============================
#   INDICADORES
# ==============================

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ==============================
#   ANALIZAR MERCADO
# ==============================

def analyze_pair(symbol_name, yahoo_symbol):
    now = dt.datetime.now(CR_TZ)
    end = now
    start = now - dt.timedelta(days=5)

    print(f"Descargando datos de {symbol_name}...")

    df = yf.download(yahoo_symbol, start=start, end=end, interval="60m")

    if df is None or df.empty:
        print(f"Sin datos suficientes para {symbol_name}")
        return None

    df["EMA_FAST"] = df["Close"].ewm(span=EMA_FAST).mean()
    df["EMA_SLOW"] = df["Close"].ewm(span=EMA_SLOW).mean()
    df["RSI"] = compute_rsi(df["Close"], RSI_PERIOD)

    last = df.iloc[-1]

    trend_up = last["EMA_FAST"] > last["EMA_SLOW"]
    trend_down = last["EMA_FAST"] < last["EMA_SLOW"]

    rsi = last["RSI"]

    # Señales
    if trend_up and rsi > RSI_BUY:
        direction = "BUY"
    elif trend_down and rsi < RSI_SELL:
        direction = "SELL"
    else:
        return None  # No señal

    entry = round(last["Close"], 5)

    if direction == "BUY":
        sl = entry - SL_PIPS * 0.0001
        tp = entry + TP_PIPS * 0.0001
    else:
        sl = entry + SL_PIPS * 0.0001
        tp = entry - TP_PIPS * 0.0001

    return {
        "pair": symbol_name,
        "direction": direction,
        "entry": entry,
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "rsi": round(rsi, 2)
    }


# ==============================
#   MAIN DEL BOT (VERSIÓN CRON)
# ==============================

def main():
    print("=== Bot ejecutándose (modo CRON) ===")

    all_signals = []

    for pair, symbol in pairs.items():
        signal = analyze_pair(pair, symbol)
        if signal:
            all_signals.append(signal)

    if not all_signals:
        print("No hubo señales esta hora.")
        return

    # Construcción del correo
    body = "SEÑALES DETECTADAS:\n\n"
    for s in all_signals:
        body += (
            f"Par: {s['pair']}\n"
            f"Dirección: {s['direction']}\n"
            f"Entry: {s['entry']}\n"
            f"SL: {s['sl']}\n"
            f"TP: {s['tp']}\n"
            f"RSI: {s['rsi']}\n"
            "--------------------------\n"
        )

    send_email("📈 Signal Bot - Nueva señal detectada", body)


if __name__ == "__main__":
    main()