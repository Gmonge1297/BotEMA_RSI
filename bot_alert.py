# bot_alert.py  --  Bot PRO: EMA20/EMA50 + RSI + ADX + 3-velas (Safe Mode)
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

# ENV (asegurar coincidencia con bot.yml)
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# Strategy parameters
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
ADX_PERIOD = 14

RSI_BUY_MIN = 55    # mínima para confirmar compra
RSI_SELL_MAX = 45   # máxima para confirmar venta
RSI_MAX_ALLOWED = 85
RSI_MIN_ALLOWED = 15

ADX_MIN = 20        # umbral mínimo de fuerza de tendencia

# SL/TP settings (Option 1: SL 30 pips TP 30 pips; gold in USD)
SL_PIPS_FOREX = 30
TP_PIPS_FOREX = 30
SL_USD_GOLD = 3
TP_USD_GOLD = 3

MAX_RISK_USD = 1.5  # riesgo por trade

TIMEFRAME_MINUTES = 60
MIN_ROWS = 60

# Safety: no more than 1 signal per pair per this many hours
SIGNAL_COOLDOWN_HOURS = 8

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

# Simple in-memory cooldown store (persists while runner runs)
last_signal_time = {}

# ============================
# UTIL: robust data handling
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
    # conservative approx per 0.0001 or 0.01 moves
    if symbol == "GC=F":       # XAUUSD
        return 0.10
    if "JPY" in symbol:
        return 0.01
    return 0.0001

def calculate_lot_for_risk(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)
    if sl_pips == 0:
        return 0.01
    # approximate value per pip for 0.01 lot
    value_0_01 = 0.10
    # conservative: if XAU use slightly smaller per pip value assumption
    if symbol == "GC=F":
        value_0_01 = 0.10
    lot = max_risk / (sl_pips * value_0_01)
    # round down to avoid overshooting risk
    lot = max(0.01, np.floor(lot * 100) / 100.0)
    return lot

# ============================
# INDICATORS
# ============================
def ema(series, span):
    s = to_1d(series)
    return s.ewm(span=span, adjust=False).mean()

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
    # Welles Wilder ADX implementation (returns ADX series)
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
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 Email enviado: {subject}")
        return True
    except Exception as e:
        print("❌ Error enviando correo:", e)
        return False

# ============================
# FETCH data (robusto)
# ============================
def fetch_ohlc_yf(symbol, period_minutes=60):
    try:
        df = yf.download(symbol, period="7d", interval=f"{period_minutes}m", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [str(c).lower() for c in df.columns]
        # remap if necessary
        if "open" not in df.columns or "close" not in df.columns:
            cand_close = [c for c in df.columns if "close" in c]
            cand_open = [c for c in df.columns if "open" in c]
            if cand_close and cand_open:
                df = df.rename(columns={cand_close[0]:"close", cand_open[0]:"open"})
            else:
                first = df.columns[0]
                df = df.rename(columns={first:"close"})
                df["open"] = df["close"]
                df["high"] = df["close"]
                df["low"] = df["close"]
                df["volume"] = 0
        # ensure numeric
        for col in ["open","high","low","close"]:
            if col in df.columns:
                if isinstance(df[col], pd.DataFrame):
                    df[col] = df[col].iloc[:,0]
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        return df
    except Exception as e:
        print("Error al descargar datos:", e)
        return pd.DataFrame()

# ============================
# SIGNAL logic (PRO)
# ============================
def analyze_pair(label, yf_symbol):
    print(f"[{now_cr()}] Analizando {label} ({yf_symbol})")
    # cooldown
    last_t = last_signal_time.get(label)
    if last_t and datetime.now() - last_t < timedelta(hours=SIGNAL_COOLDOWN_HOURS):
        print(f"  — Saltando {label}: cooldown activo ({SIGNAL_COOLDOWN_HOURS}h).")
        return

    df = fetch_ohlc_yf(yf_symbol, period_minutes=TIMEFRAME_MINUTES)
    if df.empty or len(df) < MIN_ROWS:
        print("  — Datos insuficientes.")
        return

    close = to_1d(df["close"])
    openv = to_1d(df["open"])
    high = to_1d(df["high"])
    low = to_1d(df["low"])

    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)
    adx_series = adx(high, low, close, period=ADX_PERIOD)

    # need at least ADX back values
    if len(adx_series) < ADX_PERIOD or np.isnan(adx_series.iat[-1]):
        print("  — ADX insuficiente, saltando.")
        return

    # scalar indicators for last candles
    f2 = float(ema_fast.iat[-3]); s2 = float(ema_slow.iat[-3])
    f1 = float(ema_fast.iat[-2]); s1 = float(ema_slow.iat[-2])
    fl = float(ema_fast.iat[-1]); sl = float(ema_slow.iat[-1])

    close_last = float(close.iat[-1]); open_last = float(openv.iat[-1])
    close_prev = float(close.iat[-2]); open_prev = float(openv.iat[-2])
    close_prev2 = float(close.iat[-3]); open_prev2 = float(openv.iat[-3])

    rsi_last = float(rsi_series.iat[-1])
    adx_last = float(adx_series.iat[-1])

    # debug concise
    print(f"  close={close_last:.5f} EMA20={fl:.5f} EMA50={sl:.5f} RSI={rsi_last:.1f} ADX={adx_last:.1f}")

    # cross detection (previous candle)
    cross_up_prev = (f2 <= s2) and (f1 > s1)
    cross_dn_prev = (f2 >= s2) and (f1 < s1)

    # confirmation within last 3 candles (any of the 3)
    buy_confirm_any = (
        ((close_last > open_last) and (close_last > fl) and (close_last > sl)) or
        ((close_prev > open_prev) and (close_prev > fl) and (close_prev > sl)) or
        ((close_prev2 > open_prev2) and (close_prev2 > fl) and (close_prev2 > sl))
    )
    sell_confirm_any = (
        ((close_last < open_last) and (close_last < fl) and (close_last < sl)) or
        ((close_prev < open_prev) and (close_prev < fl) and (close_prev < sl)) or
        ((close_prev2 < open_prev2) and (close_prev2 < fl) and (close_prev2 < sl))
    )

    # RSI + ADX filters
    buy_ok = (rsi_last >= RSI_BUY_MIN) and (rsi_last <= RSI_MAX_ALLOWED) and (adx_last >= ADX_MIN)
    sell_ok = (rsi_last <= RSI_SELL_MAX) and (rsi_last >= RSI_MIN_ALLOWED) and (adx_last >= ADX_MIN)

    # Final signals
    buy_signal = cross_up_prev and buy_confirm_any and buy_ok
    sell_signal = cross_dn_prev and sell_confirm_any and sell_ok

    # Explain why no signal
    if not (buy_signal or sell_signal):
        reasons = []
        if not (cross_up_prev or cross_dn_prev):
            reasons.append("sin cruce reciente")
        if not (buy_confirm_any or sell_confirm_any):
            reasons.append("sin vela confirmatoria")
        if not (adx_last >= ADX_MIN):
            reasons.append(f"ADX bajo ({adx_last:.1f} < {ADX_MIN})")
        if not (rsi_last >= RSI_BUY_MIN) and not (rsi_last <= RSI_SELL_MAX):
            reasons.append(f"RSI fuera de rango ({rsi_last:.1f})")
        print("  — No signal. Razones:", "; ".join(reasons))
        return

    # Build outputs
    if buy_signal:
        side = "BUY"
        if yf_symbol == "GC=F":
            entry = close_last
            sl = entry - SL_USD_GOLD
            tp = entry + TP_USD_GOLD
        else:
            pip = pip_value(yf_symbol)
            entry = close_last
            sl = entry - SL_PIPS_FOREX * pip
            tp = entry + TP_PIPS_FOREX * pip
    else:
        side = "SELL"
        if yf_symbol == "GC=F":
            entry = close_last
            sl = entry + SL_USD_GOLD
            tp = entry - TP_USD_GOLD
        else:
            pip = pip_value(yf_symbol)
            entry = close_last
            sl = entry + SL_PIPS_FOREX * pip
            tp = entry - TP_PIPS_FOREX * pip

    lot = calculate_lot_for_risk(entry, sl, MAX_RISK_USD, yf_symbol)

    msg = (
        f"{'📈' if side=='BUY' else '📉'} {side} Confirmado {label}\n\n"
        f"Entrada: {entry}\nSL: {sl}\nTP: {tp}\nRSI: {rsi_last:.1f}\nADX: {adx_last:.1f}\n"
        f"Riesgo por trade: ${MAX_RISK_USD}\nLote sugerido: {lot}\n"
        f"Bot: EMA20/EMA50 + RSI + ADX + 3-velas (PRO)\nGenerado: {now_cr()}\n"
    )

    # Send email and record cooldown
    ok = send_email(f"{side} Confirmado {label}", msg)
    if ok:
        last_signal_time[label] = datetime.now()
        print(f"  → Señal enviada: {side} {label} Entrada:{entry} SL:{sl} TP:{tp} Lote:{lot}")
    else:
        print("  → Señal lista pero fallo envío de email.")

# ============================
# MAIN
# ============================
if __name__ == "__main__":
    print("=== Bot PRO ejecutándose (modo CRON) ===")
    for label, symbol in pairs.items():
        analyze_pair(label, symbol)
    print("Fin del ciclo.")
