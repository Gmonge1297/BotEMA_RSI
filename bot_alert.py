import pandas as pd
import numpy as np
import yfinance as yf
import os
import datetime as dt
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ========== VARIABLES DE ENTORNO ==========
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

# ========== PARÁMETROS DE ESTRATEGIA ==========
CR_TZ = pytz.timezone("America/Costa_Rica")

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

# NUEVOS SL/TP
SL_PIPS_FOREX = 30
TP_PIPS_FOREX = 60

SL_USD_GOLD = 3
TP_USD_GOLD = 6

MAX_RISK_USD = 1.5

TIMEFRAME_MINUTES = 60

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

# ========== INDICADORES ==========
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(period).mean()
    avg_loss = pd.Series(loss).rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ========== ENVÍO DE CORREO ==========
def send_email(subject, body):
    if not EMAIL_USER or not EMAIL_PASSWORD:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return

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
        print("Correo enviado ✓:", subject)
    except Exception as e:
        print("Error enviando correo:", e)


# ========== PIP VALUE AUTOMÁTICO ==========
def pip_value(symbol):
    if symbol == "GC=F":       # ORO
        return 0.10
    if "JPY" in symbol:        # JPY
        return 0.01
    return 0.0001              # FOREX normal


# ========== LOTAJE AUTOMÁTICO ==========
def calculate_lot_for_risk(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)

    if sl_pips == 0:
        return 0.01

    # Valor por pip para 0.01 lotes
    if symbol == "GC=F":
        value_0_01 = 0.10 * 10    # XAU por lo general vale 1 USD por pip en 0.10 lot
    else:
        value_0_01 = 0.10         # Forex: 0.10 USD por pip en 0.01 lot

    lot = max_risk / (sl_pips * value_0_01)
    return max(round(lot, 2), 0.01)


# ========== DESCARGA DE DATOS ==========
def fetch_ohlc_yf(symbol, period_minutes=60):
    try:
        df = yf.download(symbol, period="7d", interval=f"{period_minutes}m", progress=False)
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
        print("Error al descargar datos:", e)
        return pd.DataFrame()


# ========== ESTRATEGIA ==========
def analyze_pair(label, yf_symbol):
    print(f"\n[🔍 Descargando datos de {label} ({yf_symbol})...]")

    df = fetch_ohlc_yf(yf_symbol)
    if df.empty or len(df) < 60:
        print("Sin datos suficientes.")
        return

    close = df["close"].astype(float)
    openv = df["open"].astype(float)

    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    # Velas prev_prev, prev, last
    f2, s2 = ema_fast.iat[-3], ema_slow.iat[-3]
    f1, s1 = ema_fast.iat[-2], ema_slow.iat[-2]
    fl, sl = ema_fast.iat[-1], ema_slow.iat[-1]

    close_last = close.iat[-1]
    open_last = openv.iat[-1]
    rsi_last = rsi_series.iat[-1]

    # Cruces
    cross_up_prev = (f2 <= s2) and (f1 > s1)
    cross_dn_prev = (f2 >= s2) and (f1 < s1)

    # Confirmaciones
    buy_confirm = close_last > open_last and close_last > fl and close_last > sl and rsi_last > RSI_BUY
    sell_confirm = close_last < open_last and close_last < fl and close_last < sl and rsi_last < RSI_SELL

    # ------------------------------------
    # BUY
    # ------------------------------------
    if cross_up_prev and buy_confirm:
        print("📈 Señal BUY detectada ✓")

        if yf_symbol == "GC=F":  # oro
            entry = close_last
