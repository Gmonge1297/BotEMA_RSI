import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from polygon import RESTClient
import os
import smtplib
from email.mime.text import MIMEText
import time

# ================= CONFIGURACIÓN =================
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

PARES = [
    ("USDJPY", "C:USDJPY"),
    ("NZDUSD", "C:NZDUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("XAUUSD", "C:XAUUSD"),
]

# FX params
TP_PIPS = 30
SL_PIPS = 20

# Oro params
TP_XAU = 800
SL_XAU = 500

# Riesgo y balance (ajusta según tu cuenta)
BALANCE = float(os.getenv("ACCOUNT_BALANCE", "1000"))   # USD
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.5"))  # %
PIP_VALUE_PER_LOT = float(os.getenv("PIP_VALUE_PER_LOT", "10"))

# Email env
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# ================= INDICADORES =================
def ema(series, span):
    return pd.Series(series).astype(float).ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_v = 100 - (100 / (1 + rs))
    return rsi_v.fillna(50)

def adx(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = (low.diff() * -1)

    plus_dm = np.where((up_move > 0) & (up_move > down_move), up_move, 0.0)
    minus_dm = np.where((down_move > 0) & (down_move > up_move), down_move, 0.0)

    tr = pd.concat([
        (high - low),
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()

    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx_v = dx.rolling(period).mean()
    return adx_v.fillna(20)

# ================= DATOS =================
def get_h1(symbol: str, days: int = 10) -> pd.DataFrame:
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)
    aggs = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="hour",
        from_=from_date.date(),
        to=to_date.date(),
        limit=50000,
    )
    df = pd.DataFrame(aggs)
    if df.empty:
        return df
    ts_col = "timestamp" if "timestamp" in df.columns else "t"
    df["timestamp"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    col_map = {"o": "open", "h": "high", "l": "low", "c": "close"}
    df = df.rename(columns=col_map)
    return df[["open", "high", "low", "close"]].dropna()

# ================= RIESGO / LOTE =================
def calc_lot(entry, sl, pip_factor, balance=BALANCE, risk_percent=RISK_PERCENT, pip_value_per_lot=PIP_VALUE_PER_LOT):
    stop_distance_price = abs(entry - sl)
    stop_distance_pips = stop_distance_price / pip_factor if pip_factor > 0 else 0
    risk_amount = balance * (risk_percent / 100.0)
    lot = risk_amount / (max(stop_distance_pips, 1e-6) * pip_value_per_lot)
    return round(risk_amount, 2), round(lot, 2)

def format_alert(label, side, entry, tp, sl, rsi_val, pip_factor):
    risk_amount, lot = calc_lot(entry, sl, pip_factor)
    arrow = "📈" if side == "BUY" else "📉"
    return (
        f"{arrow} {side} Confirmado {label}\n\n"
        f"Entrada: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}\n"
        f"RSI: {round(rsi_val, 1)}\n"
        f"Riesgo aprox: ${risk_amount}\n"
        f"Lote sugerido: {lot}\n"
    )

# ================= SEÑAL ACTUAL =================
def current_signal(label, symbol):
    df = get_h1(symbol)
    min_bars = 80 if label == "XAUUSD" else 60
    if df.empty or len(df) < min_bars:
        return None, f"{label}: ⚠️ Datos insuficientes ({len(df)} velas)"

    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    rsi_v = rsi(df["close"], 14)
    adx_v = adx(df)

    # Revisar últimas 5 velas cerradas
    for i in range(len(df)-5, len(df)):
        c_last3 = df["close"].iloc[i-3:i]
        o_last3 = df["open"].iloc[i-3:i]
        ema20_last3 = ema20.iloc[i-3:i]
        ema50_last3 = ema50.iloc[i-3:i]
        rsi_last3 = rsi_v.iloc[i-3:i]

        # Condiciones relajadas
        buy = (
            all(ema20_last3 > ema50_last3) and
            rsi_last3.mean() > 50 and
            sum(c_last3 > o_last3) >= 2
        )
        sell = (
            all(ema20_last3 < ema50_last3) and
            rsi_last3.mean() < 50 and
            sum(c_last3 < o_last3) >= 2
        )

        # Filtro oro con ADX suavizado
        if label == "XAUUSD" and adx_v.rolling(3).mean().iloc[i] < 18:
            buy, sell = False, False

        if buy or sell:
            entry = df["close"].iloc[i]
            rsi_val = rsi_v.iloc[i]

            if label == "XAUUSD":
                tp_points, sl_points, pip_factor = TP_XAU, SL_XAU, 1.0
            else:
                tp_points, sl_points, pip_factor = TP_PIPS, SL_PIPS, 0.0001

            if buy:
                tp = entry + tp_points * pip_factor
                sl = entry - sl_points * pip_factor
                alert = format_alert(label, "BUY", entry, tp, sl, rsi_val, pip_factor)
                return alert, f"{label}: BUY confirmado"

            if sell:
                tp = entry - tp_points * pip_factor
                sl = entry + sl_points * pip_factor
                alert = format_alert(label, "SELL", entry, tp, sl, rsi_val, pip_factor)
                return alert, f"{label}: SELL confirmado"

    return None, f"{label}: sin señal reciente"

# ================= EMAIL =================
def send_email(subject, body):
    if not (EMAIL_USER and EMAIL_PASSWORD and EMAIL_TO):
        return "⚠️ Email no configurado (EMAIL_USER/EMAIL_PASSWORD/EMAIL_TO)"
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        return "✅ Email enviado"
    except Exception as e:
        return f"⚠️ Error enviando email: {e}"

# ================= MAIN =================
if __name__ == "__main__":
    print("=== ALERTAS EMA20/50 + RSI (últimas 3 velas, con ADX en oro)
