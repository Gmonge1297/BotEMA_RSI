import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from polygon import RESTClient
import os
import smtplib
from email.mime.text import MIMEText
import time  # FIX: necesario para time.sleep

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
LOOKAHEAD = 20

# Oro params
TP_XAU = 800
SL_XAU = 500
LOOKAHEAD_XAU = 40

# Email env (alineado con tu log)
EMAIL_USER = os.getenv("EMAIL_USER")      # remitente
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# ================= FUNCIONES =================
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
    down_move = low.diff() * -1

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

def get_h1(symbol: str, days: int = 15) -> pd.DataFrame:  # más histórico
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

def check_signals(label, symbol):
    df = get_h1(symbol)
    min_bars = 80 if label == "XAUUSD" else 60  # relajamos umbral y adaptamos a oro
    if df.empty or len(df) < min_bars:
        return f"{label}: ⚠️ Datos insuficientes ({len(df)} velas)"

    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    rsi_v = rsi(df["close"], 14)
    adx_v = adx(df)

    if label == "XAUUSD":
        tp_points, sl_points, tp_factor, lookahead = TP_XAU, SL_XAU, 1.0, LOOKAHEAD_XAU
    else:
        tp_points, sl_points, tp_factor, lookahead = TP_PIPS, SL_PIPS, 0.0001, LOOKAHEAD

    signals = []
    for i in range(52, len(df) - lookahead):
        c_last3 = df["close"].iloc[i-3:i]
        o_last3 = df["open"].iloc[i-3:i]
        ema20_last3 = ema20.iloc[i-3:i]
        ema50_last3 = ema50.iloc[i-3:i]
        rsi_last3 = rsi_v.iloc[i-3:i]

        buy = (
            all(ema20_last3 > ema50_last3) and
            all(rsi_last3 > 55) and
            all(c_last3 > o_last3)
        )
        sell = (
            all(ema20_last3 < ema50_last3) and
            all(rsi_last3 < 45) and
            all(c_last3 < o_last3)
        )

        # Filtro extra para oro: ADX > 20 (fuerza de tendencia)
        if label == "XAUUSD" and adx_v.iloc[i] < 20:
            buy, sell = False, False

        if buy or sell:
            entry = df["close"].iloc[i]
            future = df.iloc[i+1:i+lookahead]

            if buy:
                tp = entry + tp_points * tp_factor
                sl = entry - sl_points * tp_factor
                if (future["high"] >= tp).any():
                    signals.append(f"{label} BUY ✅ TP alcanzado")
                elif (future["low"] <= sl).any():
                    signals.append(f"{label} BUY ❌ SL alcanzado")
                else:
                    signals.append(f"{label} BUY ⏸ Neutro")

            if sell:
                tp = entry - tp_points * tp_factor
                sl = entry + sl_points * tp_factor
                if (future["low"] <= tp).any():
                    signals.append(f"{label} SELL ✅ TP alcanzado")
                elif (future["high"] >= sl).any():
                    signals.append(f"{label} SELL ❌ SL alcanzado")
                else:
                    signals.append(f"{label} SELL ⏸ Neutro")

    if signals:
        body = "\n".join(signals)
        email_status = send_email(f"Señales {label}", body)
        return f"{label}: {len(signals)} señales enviadas ({email_status})"
    else:
        return f"{label}: sin señales"

# ================= MAIN =================
if __name__ == "__main__":
    print("=== ALERTAS EMA20/50 + RSI (últimas 3 velas, con ADX en oro) ===")
    for label, symbol in PARES:
        try:
            result = check_signals(label, symbol)
            print(result)
            time.sleep(15)  # pausa larga para evitar límite de API
        except Exception as e:
            print(f"{label}: ⚠️ Error {e}")
