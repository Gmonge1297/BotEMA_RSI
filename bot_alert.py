import pandas as pd
import numpy as np
import yfinance as yf
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ============================
#     VARIABLES DE ENTORNO
# ============================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# ============================
#     PARÁMETROS ESTRATEGIA
# ============================
PERIOD = "7d"
INTERVAL = "1h"

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

SL_PIPS = 300
TP_PIPS = 600
MAX_RISK_USD = 1.5

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

# ============================
#     INDICADORES
# ============================
def rsi(series, period=14):
    """RSI estable y sin errores de dimensionalidad."""
    series = pd.Series(series).astype(float)

    delta = series.diff()

    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)

    gain = pd.Series(gain).rolling(period).mean()
    loss = pd.Series(loss).rolling(period).mean()

    rs = gain / loss
    rsi_series = 100 - (100 / (1 + rs))

    return rsi_series


# ============================
#     ENVÍO DE CORREO
# ============================
def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtpltr.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()

        print("Correo enviado:", subject)

    except Exception as e:
        print("Error enviando correo:", e)


# ============================
#     PIP VALUE / RIESGO
# ============================
def pip_value(symbol):
    return 0.01 if "XAU" in symbol else 0.0001

def calculate_lot_for_risk(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)

    if sl_pips == 0:
        return 0.01

    value_per_pip_per_0_01 = 0.10
    lot = max_risk / (sl_pips * value_per_pip_per_0_01)

    return max(round(lot, 2), 0.01)


# ============================
# DESCARGA DE DATOS YF
# ============================
def fetch(symbol):
    try:
        df = yf.download(symbol, period=PERIOD, interval=INTERVAL, progress=False)
        if df.empty:
            return pd.DataFrame()

        df = df.rename(columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        })

        return df

    except Exception as e:
        print("Error descargando datos:", e)
        return pd.DataFrame()


# ============================
#      LÓGICA DE SEÑALES
# ============================
def obtener_senal(df):
    close = df["close"]
    open_ = df["open"]

    ema20 = close.ewm(span=EMA_FAST, adjust=False).mean()
    ema50 = close.ewm(span=EMA_SLOW, adjust=False).mean()
    rsi_val = rsi(close, RSI_PERIOD)

    # Extraer últimas 3 velas
    c0, c1, c2 = close.iloc[-1], close.iloc[-2], close.iloc[-3]
    o0, o1, o2 = open_.iloc[-1], open_.iloc[-2], open_.iloc[-3]

    ema_fast = ema20.iloc[-1]
    ema_slow = ema50.iloc[-1]
    rsi_last = rsi_val.iloc[-1]

    # BUY
    buy_cond = (
        ema_fast > ema_slow and
        rsi_last > 50 and
        ((c0 > o0) or (c1 > o1) or (c2 > o2))
    )

    if buy_cond:
        return "BUY", c0

    # SELL
    sell_cond = (
        ema_fast < ema_slow and
        rsi_last < 50 and
        ((c0 < o0) or (c1 < o1) or (c2 < o2))
    )

    if sell_cond:
        return "SELL", c0

    return None, None


# ============================
#     REVISAR PAR
# ============================
def revisar_par(name, symbol):
    print(f"[🔍 Descargando datos de {name} ({symbol})...]")

    df = fetch(symbol)
    if df.empty or len(df) < 60:
        print("Sin datos suficientes.")
        return

    senal, price = obtener_senal(df)
    if senal is None:
        print(f"No hay señal en {name}.")
        return

    pip = pip_value(symbol)

    if senal == "BUY":
        sl = price - SL_PIPS * pip
        tp = price + TP_PIPS * pip

    else:
        sl = price + SL_PIPS * pip
        tp = price - TP_PIPS * pip

    lot = calculate_lot_for_risk(price, sl, MAX_RISK_USD, symbol)

    # Mensaje
    body = f"""
📡 Señal {senal} detectada en {name}

Precio: {price}
SL: {sl}
TP: {tp}
RSI: (calculado)
Lote sugerido: {lot}
"""

    send_email(f"{senal} - {name}", body)


# ============================
#     MAIN
# ============================
if __name__ == "__main__":
    print("=== Bot Intermedio: EMA20/EMA50 + RSI + Últimas 3 velas ===")

    for name, symbol in pairs.items():
        revisar_par(name, symbol)

    print("Fin del ciclo.")
    
