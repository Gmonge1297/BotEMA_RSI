import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import smtplib
from email.mime.text import MIMEText

# ================================
# CONFIGURACIÓN
# ================================
INTERVAL = "1h"
PERIOD = "2d"   # siempre devuelve OHLC correctamente

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

EMAIL_ENABLED = True
EMAIL_FROM = "BOT@gmail.com"
EMAIL_TO = "TU_CORREO"
EMAIL_PASS = "APP_PASSWORD"

COSTA_RICA_TZ = pytz.timezone("America/Costa_Rica")


# ================================
# FUNCIÓN: ENVIAR EMAIL
# ================================
def send_email(subject, body):
    if not EMAIL_ENABLED:
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        server.quit()
        print("📧 Email enviado.")
    except Exception as e:
        print("❌ Error enviando email:", e)


# ================================
# FUNCIÓN: DESCARGAR DATA OHLC CORRECTA
# ================================
def download_clean(symbol):
    """
    Yahoo Finance a veces devuelve columnas con MultiIndex.
    Esta función renombra correctamente los OHLC a:
    ['Open', 'High', 'Low', 'Close', 'Volume']
    """
    df = yf.download(symbol, period=PERIOD, interval=INTERVAL, progress=False)

    if df.empty:
        print(f"❌ No hay datos para {symbol}")
        return None

    # Si trae MultiIndex (típico en Forex)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    # A veces devuelve columnas duplicadas iguales: Close, Close, Close...
    uniques = pd.unique(df.columns)
    if len(uniques) == 1:
        # entonces es: ['EURUSD=X', 'EURUSD=X', 'EURUSD=X', ...]
        # mapeamos manualmente
        df.columns = ["Open", "High", "Low", "Close", "Adj Close"]
        df["Volume"] = 0

    # Validación final
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(set(df.columns)):
        print(f"⚠️ Data no contiene columnas OHLC necesarias. Columnas: {list(df.columns)}")
        return None

    return df


# ================================
# INDICADORES (EMA y RSI)
# ================================
def compute_indicators(df):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()

    # RSI
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df


# ================================
# FUNCIÓN: GENERAR SEÑAL
# ================================
def check_signal(symbol, df):
    df = df.dropna()
    if len(df) < 3:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Tendencia
    ema_bull = last["EMA20"] > last["EMA50"]
    ema_bear = last["EMA20"] < last["EMA50"]

    # Velas
    bullish_candle = last["Close"] > last["Open"]
    bearish_candle = last["Close"] < last["Open"]

    # Señal BUY
    if ema_bull and prev["RSI"] < 30 and last["RSI"] > 30 and bullish_candle:
        return {
            "type": "BUY",
            "price": last["Close"],
            "sl": last["Low"],
            "tp": last["Close"] + (last["Close"] - last["Low"]) * 2
        }

    # Señal SELL
    if ema_bear and prev["RSI"] > 70 and last["RSI"] < 70 and bearish_candle:
        return {
            "type": "SELL",
            "price": last["Close"],
            "sl": last["High"],
            "tp": last["Close"] - (last["High"] - last["Close"]) * 2
        }

    return None


# ================================
# PROGRAMA PRINCIPAL
# ================================
def run_bot():
    print("\n=== Bot EMA + RSI ===\n")

    for name, ticker in SYMBOLS.items():

        print(f"[ {datetime.now(COSTA_RICA_TZ)} ] Descargando datos de {name} ({ticker})...")

        df = download_clean(ticker)

        if df is None:
            continue

        df = compute_indicators(df)

        signal = check_signal(name, df)

        if signal is None:
            print("ℹ️ Sin señales.\n")
            continue

        # ===== MOSTRAR ALERTA =====
        msg = (
            f"📊 Señal detectada en {name}\n"
            f"Tipo: {signal['type']}\n"
            f"Precio entrada: {signal['price']}\n"
            f"SL: {signal['sl']}\n"
            f"TP: {signal['tp']}\n"
            f"Hora CR: {datetime.now(COSTA_RICA_TZ)}"
        )

        print(msg)
        send_email(f"Señal {signal['type']} - {name}", msg)
        print("")

    print("\n=== Fin ejecución ===\n")


# Ejecutar
if __name__ == "__main__":
    run_bot()
