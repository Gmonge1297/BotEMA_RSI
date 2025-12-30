# bot_alert_ema20_50_rsi_safe.py
import os
import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np
from polygon import RESTClient
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= CONFIGURACIÓN =================
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

SL_PIPS = 30
TP_PIPS = 30
MAX_RISK_USD = 1.50

COOLDOWN_HOURS = 1
STATE_FILE = "ema_state.json"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

PARES = [
    ("EURUSD", "C:EURUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("USDJPY", "C:USDJPY"),
    ("AUDUSD", "C:AUDUSD"),
    ("NZDUSD", "C:NZDUSD"),
    ("USDCAD", "C:USDCAD"),
    ("XAUUSD", "C:XAUUSD"),  # Oro
]

# ================= UTILIDADES =================
def pip_size(symbol: str) -> float:
    if "JPY" in symbol:
        return 0.01
    if "XAUUSD" in symbol:
        return 0.1
    return 0.0001

def ema(series, span):
    return pd.Series(series).astype(float).ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

# ================= ESTADO =================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ================= LOTAJE =================
def lot_size(entry: float, sl: float, symbol: str) -> float:
    pip_sz = pip_size(symbol)
    stop_pips = abs(entry - sl) / pip_sz
    pip_value = 10.0 if "JPY" not in symbol else 9.0
    if "XAUUSD" in symbol:
        pip_value = 1.0
    if stop_pips <= 0:
        return 0.01
    lot = MAX_RISK_USD / (stop_pips * pip_value)
    return max(round(lot, 2), 0.01)

# ================= EMAIL =================
def send_email(subject, body):
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("📧 Email enviado")
    except Exception as e:
        print(f"⚠️ Error enviando email: {e}")

# ================= DATOS =================
def get_h1(symbol: str, days: int = 30) -> pd.DataFrame:
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)

    try:
        aggs = client.get_aggs(
            ticker=symbol,
            multiplier=1,
            timespan="hour",
            from_=from_date.date(),
            to=to_date.date(),
            limit=50000,
        )
    except Exception as e:
        # Manejo especial para rate limit
        if "429" in str(e):
            print(f"  ⚠️ Rate limit alcanzado en {symbol}, esperando 60s...")
            time.sleep(60)
        else:
            print(f"  ⚠️ Error al obtener datos de {symbol}: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(aggs)
    if df.empty:
        return df

    ts_col = "timestamp" if "timestamp" in df.columns else "t"
    df["timestamp"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)

    col_map = {"o": "open", "h": "high", "l": "low", "c": "close"}
    df = df.rename(columns=col_map)
    return df[["open", "high", "low", "close"]].dropna()

# ================= LÓGICA =================
def analyze(label: str, symbol: str, state: dict):
    print(f"\n→ Analizando {label}...")

    now = datetime.now(timezone.utc)
    last_ts_iso = state.get(label)
    if last_ts_iso:
        last_ts = datetime.fromisoformat(last_ts_iso)
        if (now - last_ts).total_seconds() < COOLDOWN_HOURS * 3600:
            print("  ⏸️ Cooldown activo")
            return

    df = get_h1(symbol)
    if df is None or len(df) < 60:
        print("  ⚠️ Datos insuficientes o error técnico")
        return

    close = df["close"]
    open_ = df["open"]

    ema20 = ema(close, EMA_FAST)
    ema50 = ema(close, EMA_SLOW)
    rsi_v = rsi(close, RSI_PERIOD)

    c0, o0 = close.iloc[-2], open_.iloc[-2]

    print(
        f"  EMA20: {ema20.iloc[-2]:.5f} | EMA50: {ema50.iloc[-2]:.5f} | "
        f"RSI: {rsi_v.iloc[-2]:.2f} | Cierre: {c0:.5f}"
    )

    buy = (
        ema20.iloc[-3] <= ema50.iloc[-3]
        and ema20.iloc[-2] > ema50.iloc[-2]
        and rsi_v.iloc[-2] >= 50
        and c0 > o0
    )

    sell = (
        ema20.iloc[-3] >= ema50.iloc[-3]
        and ema20.iloc[-2] < ema50.iloc[-2]
        and rsi_v.iloc[-2] <= 50
        and c0 < o0
    )

    if not (buy or sell):
        print("  ❌ No hay señal en esta vela")
        return

    direction = "BUY" if buy else "SELL"
    entry = c0
    pip_sz = pip_size(symbol)
    sl = entry - SL_PIPS * pip_sz if buy else entry + SL_PIPS * pip_sz
    tp = entry + TP_PIPS * pip_sz if buy else entry - TP_PIPS * pip_sz
    lot = lot_size(entry, sl, symbol)

    mt5_symbol = "GOLD" if "XAUUSD" in symbol else label

    msg = f"""✅ SEÑAL {direction} {label} (MT5: {mt5_symbol})

Estrategia: EMA {EMA_FAST}/{EMA_SLOW} + RSI {RSI_PERIOD}
Timeframe: H1 (vela cerrada)

Entrada: {entry:.5f}
SL: {sl:.5f}
TP: {tp:.5f}
Lote sugerido: {lot}
"""

    send_email(f"{direction} {label}", msg)
    state[label] = now.isoformat()
    save_state(state)

# ================= MAIN =================
if __name__ == "__main__":
    print(f"=== BOT EMA20/50 + RSI (UTC {datetime.now(timezone.utc).strftime('%H:%M')}) ===")
    state = load_state()

    for label, symbol in PARES:
        try:
            analyze(label, symbol, state)
            time.sleep(5)  # más tiempo entre pares para evitar rate limit
        except Exception as e:
            print(f"  ⚠️ Error analizando {label}: {e}")

    print("\nCiclo terminado 🚀")
