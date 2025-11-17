import pandas as pd
import numpy as np
import datetime as dt
import pytz
import time
from fmp_python.fmp import FMP
import os

# ---------- Variables de entorno ----------
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

FMP_API_KEY = os.getenv("FMP_API_KEY")

# ---------- Parámetros de Estrategia ----------
CR_TZ = pytz.timezone("America/Costa_Rica")

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

SL_PIPS = 300
TP_PIPS = 600
MAX_RISK_USD = 1.5
TIMEFRAME_MINUTES = 60

pairs = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "XAUUSD": "XAUUSD"
}

# ---------- Indicadores ----------
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(window=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series

# ---------- Envío de correo ----------
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
        server.send_message(msg)
        server.quit()
        print("Correo enviado:", subject)
    except Exception as e:
        print("Error enviando correo:", e)

# ---------- Pips ----------
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

# ---------- Datos desde FMP ----------
def fetch_fmp(symbol):
    try:
        fmp = FMP(FMP_API_KEY)
        tf = "1hour"

        data = fmp.forex_candle(pair=symbol, interval=tf, limit=200)
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df = df.rename(columns={
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close"
        })

        df = df[["open", "high", "low", "close"]].astype(float)
        return df

    except Exception as e:
        print("Error FMP:", e)
        return pd.DataFrame()

# ---------- Estrategia con Vela de Confirmación ----------
def analyze_pair(label, symbol):
    print(f"Descargando datos de {label}...")

    df = fetch_fmp(symbol)
    if df.empty or len(df) < 60:
        print("Sin datos suficientes.")
        return

    close = df["close"]
    openv = df["open"]

    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    f2 = ema_fast.iat[-3]
    s2 = ema_slow.iat[-3]
    f1 = ema_fast.iat[-2]
    s1 = ema_slow.iat[-2]
    fl = ema_fast.iat[-1]
    sl = ema_slow.iat[-1]

    close1 = close.iat[-2]
    close_last = close.iat[-1]
    open_last = openv.iat[-1]

    rsi_last = rsi_series.iat[-1]

    cross_up_prev = (f2 <= s2) and (f1 > s1)
    cross_dn_prev = (f2 >= s2) and (f1 < s1)

    buy_confirm = (
        close_last > open_last and
        close_last > fl and close_last > sl and
        rsi_last > RSI_BUY
    )

    sell_confirm = (
        close_last < open_last and
        close_last < fl and close_last < sl and
        rsi_last < RSI_SELL
    )

    # ---- BUY ----
    if cross_up_prev and buy_confirm:
        entry = float(close_last)
        slv = entry - SL_PIPS * pip_value(symbol)
        tpv = entry + TP_PIPS * pip_value(symbol)
        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, symbol)

        msg = f"""📈 Señal CONFIRMADA BUY {label}

Entrada: {entry}
SL: {slv}
TP: {tpv}
RSI: {rsi_last:.1f}
Riesgo: ${MAX_RISK_USD}
Lote sugerido: {lot}
"""
        send_email(f"BUY Confirmado {label}", msg)
        return

    # ---- SELL ----
    if cross_dn_prev and sell_confirm:
        entry = float(close_last)
        slv = entry + SL_PIPS * pip_value(symbol)
        tpv = entry - TP_PIPS * pip_value(symbol)
        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, symbol)

        msg = f"""📉 Señal CONFIRMADA SELL {label}

Entrada: {entry}
SL: {slv}
TP: {tpv}
RSI: {rsi_last:.1f}
Riesgo: ${MAX_RISK_USD}
Lote sugerido: {lot}
"""
        send_email(f"SELL Confirmado {label}", msg)
        return

# ---------- Loop principal ----------
if __name__ == "__main__":
    print("=== Bot ejecutándose (modo CRON) ===")

    for label, symbol in pairs.items():
        analyze_pair(label, symbol)
        time.sleep(2)

    print("No hubo señales esta hora.")
