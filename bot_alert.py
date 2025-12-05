# bot_alert.py — Versión con diagnóstico de columnas YF
import os
import yfinance as yf
import pandas as pd
import numpy as np
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# ============================
# CONFIG
# ============================
CR_TZ = pytz.timezone("America/Costa_Rica")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ADX_PERIOD = 14

RSI_BUY_MIN = 55
RSI_SELL_MAX = 45
RSI_MAX_ALLOWED = 85
RSI_MIN_ALLOWED = 15

ADX_MIN = 20

SL_PIPS_FOREX = 30
TP_PIPS_FOREX = 30
SL_USD_GOLD = 3
TP_USD_GOLD = 3

MAX_RISK_USD = 1.5

TIMEFRAME_MINUTES = 60
MIN_ROWS = 50

SIGNAL_COOLDOWN_HOURS = 8

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

last_signal_time = {}

# ============================
# UTIL
# ============================
def now_cr():
    return datetime.now(CR_TZ).strftime("%Y-%m-%d %H:%M:%S")

def to_1d(series):
    if isinstance(series, pd.DataFrame):
        s = series.iloc[:, 0]
        return pd.Series(s).astype(float).reset_index(drop=True)
    arr = np.asarray(series)
    if arr.ndim == 2 and arr.shape[1] == 1:
        arr = arr.ravel()
        return pd.Series(arr).astype(float).reset_index(drop=True)
    return pd.Series(series).astype(float).reset_index(drop=True)

def pip_value(symbol):
    if symbol == "GC=F":
        return 0.10
    if "JPY" in symbol:
        return 0.01
    return 0.0001

def calculate_lot_for_risk(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)
    if sl_pips == 0:
        return 0.01
    value_0_01 = 0.10
    if symbol == "GC=F":
        value_0_01 = 0.10
    lot = max_risk / (sl_pips * value_0_01)
    lot = max(0.01, np.floor(lot * 100) / 100.0)
    return lot

# ============================
# INDICATORS
# ============================
def ema(series, span):
    return to_1d(series).ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    s = to_1d(series)
    delta = s.diff()
    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)
    avg_gain = pd.Series(gain).rolling(window=period, min_periods=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series.fillna(50)

def adx(high, low, close, period=14):
    h = to_1d(high)
    l = to_1d(low)
    c = to_1d(close)
    plus_dm = h.diff()
    minus_dm = -l.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period, min_periods=period).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(window=period, min_periods=period).mean() / atr.replace(0, np.nan))
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)).replace([np.inf, -np.inf], np.nan) * 100
    adx = dx.rolling(window=period, min_periods=period).mean()
    return adx.fillna(0)

# ============================
# EMAIL
# ============================
def send_email(subject, body):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Email cfg incompleto.")
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
    except Exception as e:
        print("❌ Email error:", e)

# ============================
# FETCH OHLC — versión con debug
# ============================
def fetch_ohlc_yf(symbol, period_minutes=60):
    try:
        df = yf.download(
            symbol,
            period="7d",
            interval=f"{period_minutes}m",
            progress=False,
            auto_adjust=False
        )

        if df is None or df.empty:
            raise Exception("Datos vacíos del servidor")

        # 🔥 DEBUG NUEVO — EL QUE NECESITO
        print("\n=== DEBUG COLUMNAS ===")
        print("Symbol:", symbol)
        print("COLUMNAS:", df.columns)
        print(df.head())
        print("=====================\n")

        return df

    except Exception as e:
        print("[ERROR descarga]:", e)
        return pd.DataFrame()

# ============================
# SIGNAL LOGIC
# ============================
def analyze_pair(label, yf_symbol):
    print(f"[{now_cr()}] Analizando {label} ({yf_symbol})")

    df = fetch_ohlc_yf(yf_symbol, period_minutes=TIMEFRAME_MINUTES)
    if df.empty or len(df) < MIN_ROWS:
        print("  — Datos insuficientes.\n")
        return

    # Intentamos usar lower-case estándar
    df.columns = [str(c).lower() for c in df.columns]

    close = df["close"]
    high = df["high"]
    low = df["low"]
    openv = df["open"]

    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)

    rsi_series = rsi(close, RSI_PERIOD)
    adx_series = adx(high, low, close, period=ADX_PERIOD)

    if len(ema_fast) < 3 or len(ema_slow) < 3:
        print("  — EMAs insuficientes.\n")
        return

    close_l = float(close.iloc[-1])
    open_l = float(openv.iloc[-1])

    ema_fast_l = float(ema_fast.iloc[-1])
    ema_slow_l = float(ema_slow.iloc[-1])

    rsi_l = float(rsi_series.iloc[-1])
    adx_l = float(adx_series.iloc[-1])

    # Señal simple
    if ema_fast_l > ema_slow_l and rsi_l > RSI_BUY_MIN and adx_l > ADX_MIN:
        entry = close_l
        sl = entry - SL_PIPS_FOREX * pip_value(yf_symbol)
        tp = entry + TP_PIPS_FOREX * pip_value(yf_symbol)

        print(f"\nSEÑAL {label}: BUY")
        print("  Entrada:", entry)
        print("  SL:", sl)
        print("  TP:", tp, "\n")
        return

    if ema_fast_l < ema_slow_l and rsi_l < RSI_SELL_MAX and adx_l > ADX_MIN:
        entry = close_l
        sl = entry + SL_PIPS_FOREX * pip_value(yf_symbol)
        tp = entry - TP_PIPS_FOREX * pip_value(yf_symbol)

        print(f"\nSEÑAL {label}: SELL")
        print("  Entrada:", entry)
        print("  SL:", sl)
        print("  TP:", tp, "\n")
        return

# ============================
# MAIN LOOP
# ============================
print(f"[{now_cr()}] === Bot PRO ejecutándose (modo CRON) ===\n")

for label, symbol in pairs.items():
    analyze_pair(label, symbol)

print(f"[{now_cr()}] === Fin del ciclo PRO ===\n")
