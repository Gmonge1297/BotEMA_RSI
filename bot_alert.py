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

TP_PIPS = 30
SL_PIPS = 20
TP_XAU = 800
SL_XAU = 500

FIXED_RISK_USD = 1.50
PIP_VALUE_PER_LOT = 10

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465

LAST_SIGNAL_FILE = "last_signal.txt"

# ================= CONTROL DE DUPLICADOS =================
def already_sent(label, ts):
    if not os.path.exists(LAST_SIGNAL_FILE):
        return False
    with open(LAST_SIGNAL_FILE, "r") as f:
        return f"{label}:{ts}" in f.read().splitlines()

def mark_sent(label, ts):
    with open(LAST_SIGNAL_FILE, "a") as f:
        f.write(f"{label}:{ts}\n")

# ================= INDICADORES =================
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)

def adx(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0)
    minus_dm = np.where((down > up) & (down > 0), down, 0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    return dx.rolling(period).mean().fillna(20)

# ================= DATOS =================
def get_h1(symbol, days=10):
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

    # 🔥 Detectar automáticamente la columna de tiempo
    if "t" in df.columns:
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    else:
        print("Columnas recibidas de Polygon:", df.columns)
        raise ValueError("Polygon no envió columna de tiempo válida")

    df.set_index("timestamp", inplace=True)

    df = df.rename(columns={
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close"
    })

    return df[["open", "high", "low", "close"]].dropna()
    # ================= FORMATO ALERTA =================
def format_alert(label, side, entry, tp, sl, pip_factor):
    stop_distance_price = abs(entry - sl)
    stop_distance_pips = stop_distance_price / pip_factor
    lot = FIXED_RISK_USD / (stop_distance_pips * PIP_VALUE_PER_LOT)
    lot = round(max(lot, 0.01), 2)

    arrow = "📉" if side == "SELL" else "📈"

    return (
        f"{arrow} {side} {label}\n\n"
        f"Entrada: {round(entry, 5)}\n"
        f"SL: {round(sl, 5)}\n"
        f"TP: {round(tp, 5)}\n"
        f"Lote sugerido: {lot}\n"
        f"Riesgo máximo: ${FIXED_RISK_USD}\n"
    )

# ================= LÓGICA DE SEÑAL =================
def current_signal(label, symbol):
    df = get_h1(symbol)
    if df.empty or len(df) < 60:
        return None, f"{label}: datos insuficientes"

    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    rsi_v = rsi(df["close"], 14)
    adx_v = adx(df)

    i = len(df) - 2       # vela cerrada
    live_i = len(df) - 1  # vela actual en formación

    ts = df.index[i]
    entry = df.iloc[live_i]["close"]

    # ⏰ SOLO operar si la vela cerró hace menos de 70 minutos
    now = datetime.now(timezone.utc)
    if now - ts > timedelta(minutes=70):
        return None, f"{label}: señal vieja ignorada ({ts})"

    if already_sent(label, ts):
        return None, f"{label}: señal ya enviada"

    ema20_3 = ema20.iloc[i-3:i]
    ema50_3 = ema50.iloc[i-3:i]
    rsi_3 = rsi_v.iloc[i-3:i]
    closes_3 = df["close"].iloc[i-3:i]
    opens_3 = df["open"].iloc[i-3:i]

    buy = all(ema20_3 > ema50_3) and rsi_3.mean() > 50 and sum(closes_3 > opens_3) >= 2
    sell = all(ema20_3 < ema50_3) and rsi_3.mean() < 50 and sum(closes_3 < opens_3) >= 2

    if label == "XAUUSD" and adx_v.iloc[i] < 18:
        buy = sell = False

    if not (buy or sell):
        return None, f"{label}: sin señal"

        # Parámetros según activo
    if label == "XAUUSD":
        pip_factor = 1.0
        sl_pips = SL_XAU
        tp_pips = TP_XAU
    elif "JPY" in label:
        pip_factor = 0.01
        sl_pips = SL_PIPS
        tp_pips = TP_PIPS
    else:
        pip_factor = 0.0001
        sl_pips = SL_PIPS
        tp_pips = TP_PIPS

    # Calcular SL y TP
    if buy:
        sl = entry - sl_pips * pip_factor
        tp = entry + tp_pips * pip_factor
        alert = format_alert(label, "BUY", entry, tp, sl, pip_factor)

    elif sell:
        sl = entry + sl_pips * pip_factor
        tp = entry - tp_pips * pip_factor
        alert = format_alert(label, "SELL", entry, tp, sl, pip_factor)

    else:
        return None, f"{label}: error interno"

    mark_sent(label, ts)
    return alert, f"{label}: señal enviada"

# ================= EMAIL =================
def send_email(subject, body):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        return "Email no configurado"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        return "Email enviado"
    except Exception as e:
        return f"Error email: {e}"

# ================= MAIN (GITHUB SCHEDULER) =================
if __name__ == "__main__":
    print("=== BOT EMA20/50 + RSI (GitHub Scheduler) ===")

    any_alert = False

    for label, symbol in PARES:
        try:
            alert, status = current_signal(label, symbol)
            print(status)

            if alert:
                any_alert = True
                subject = f"Señal {label}"
                body = f"{status}\n\n{alert}"
                email_status = send_email(subject, body)
                print(f"{label}: {email_status}")

        except Exception as e:
            print(f"{label}: error {e}")

    if not any_alert:
        print("Sin señales en esta ejecución.")

    print("Bot finalizado correctamente.")
