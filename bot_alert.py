import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from polygon import RESTClient
import os
import smtplib
from email.mime.text import MIMEText

# ================= CONFIGURACIÓN =================
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

PARES = [
    ("USDJPY", "C:USDJPY"),
    ("NZDUSD", "C:NZDUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("XAUUSD", "C:XAUUSD"),
]

TP_PIPS = 30    # FX TP
SL_PIPS = 20    # FX SL
LOOKAHEAD = 20  # FX ventana

TP_XAU = 800    # Oro TP
SL_XAU = 500    # Oro SL
LOOKAHEAD_XAU = 40  # Oro ventana

EMAIL_TO = os.getenv("ALERT_EMAIL")
EMAIL_FROM = os.getenv("BOT_EMAIL")
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PASS = os.getenv("SMTP_PASS")

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
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def adx(df, period=14):
    # ADX para medir fuerza de tendencia
    high = df["high"]
    low = df["low"]
    close = df["close"]

    plus_dm = high.diff()
    minus_dm = low.diff()

    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0.0)

    tr1 = pd.DataFrame(high - low)
    tr2 = pd.DataFrame(abs(high - close.shift(1)))
    tr3 = pd.DataFrame(abs(low - close.shift(1)))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()

    plus_di = 100 * (pd.Series(plus_dm).rolling(period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(period).mean() / atr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    adx = dx.rolling(period).mean()
    return adx.fillna(20)

def get_h1(symbol: str, days: int = 5) -> pd.DataFrame:
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
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    with smtplib.SMTP_SSL(SMTP_SERVER, 465) as server:
        server.login(EMAIL_FROM, SMTP_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

def check_signals(label, symbol):
    df = get_h1(symbol)
    if df.empty or len(df) < 60:
        return f"{label}: ⚠️ Datos insuficientes"

    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    rsi_v = rsi(df["close"], 14)
    adx_v = adx(df)

    # Configuración dinámica
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

        # Filtro extra para oro: ADX > 20
        if label == "XAUUSD":
            if adx_v.iloc[i] < 20:
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
        send_email(f"Señales {label}", body)
        return f"{label}: {len(signals)} señales enviadas"
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
