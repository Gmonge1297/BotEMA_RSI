# bot_alert.py → VERSIÓN AJUSTADA: Más sensible para pullbacks en tendencias (captura rallies como oro/EURUSD)
import os
import pandas as pd
import numpy as np
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from polygon import RESTClient

# CONFIG
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

EMA_FAST = 8
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_BUY = 53   # Más sensible
RSI_SELL = 47
ATR_PERIOD = 14
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.8
MAX_RISK_USD = 1.5

PARES = [
    ("EURUSD", "C:EURUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("XAUUSD", "C:XAUUSD")
]

# UTILIDADES (igual)
def to_1d(s):
    if isinstance(s, pd.DataFrame): s = s.iloc[:, 0]
    return pd.Series(s).astype(float).reset_index(drop=True)

def ema(series, span):
    return to_1d(series).ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    s = to_1d(series)
    delta = s.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs)).fillna(50)

def atr(high, low, close, period=14):
    tr = pd.concat([
        to_1d(high) - to_1d(low),
        abs(to_1d(high) - to_1d(close).shift()),
        abs(to_1d(low) - to_1d(close).shift())
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def pip_value(symbol):
    if "XAUUSD" in symbol: return 0.1
    if "JPY" in symbol: return 0.01
    return 0.0001

def lot_size(entry, sl, symbol):
    pips = abs(entry - sl) / pip_value(symbol)
    if pips <= 0: return 0.01
    lot = MAX_RISK_USD / (pips * 1.0)
    return max(round(lot, 2), 0.01)

def send_email(subject, body):
    if not all([EMAIL_USER, EMAIL_PASSWORD, EMAIL_TO]):
        print("Email no configurado")
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
        print("EMAIL ENVIADO")
    except Exception as e:
        print("Error email:", e)

def get_data(symbol, timeframe, days=12):
    if not POLYGON_API_KEY:
        print("Falta POLYGON_API_KEY")
        return pd.DataFrame()
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now()
    from_date = to_date - timedelta(days=days)
    try:
        aggs = client.get_aggs(
            ticker=symbol,
            multiplier=1,
            timespan=timeframe,
            from_=from_date.date(),
            to=to_date.date(),
            limit=50000
        )
        df = pd.DataFrame(aggs)
        if df.empty: return pd.DataFrame()
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df[['open', 'high', 'low', 'close']]
    except Exception as e:
        print("Error Polygon:", e)
        return pd.DataFrame()

def analyze(label, symbol):
    print(f"\n→ Analizando {label}...")
    df1 = get_data(symbol, "hour", days=15)  # Más datos para mejor detección
    time.sleep(2)
    df4 = get_data(symbol, "day", days=80)

    if df1.empty or len(df1) < 100 or df4.empty:
        print("  Sin datos suficientes")
        return

    close = df1['close']
    open_ = df1['open']
    high  = df1['high']
    low   = df1['low']

    ema8  = ema(close, EMA_FAST)
    ema21 = ema(close, EMA_SLOW)
    rsi_v = rsi(close, RSI_PERIOD)
    atr_v = atr(high, low, close, ATR_PERIOD)

    ema50_d  = ema(df4['close'], 50)
    ema200_d = ema(df4['close'], 200)
    trend_up   = ema50_d.iloc[-1] > ema200_d.iloc[-1]
    trend_down = ema50_d.iloc[-1] < ema200_d.iloc[-1]

    c0 = close.iloc[-1]
    o0 = open_.iloc[-1]
    e8_0  = ema8.iloc[-1]
    e21_0 = ema21.iloc[-1]
    rsi_now = rsi_v.iloc[-1]
    atr_now = atr_v.iloc[-1]

    # AJUSTE NUEVO: Más sensible para pullbacks en tendencia establecida
    buy_setup  = (
        (c0 > o0) and (c0 > e21_0) and (close.iloc[-2] <= e21_0) and  # Pullback previo + vela fuerte actual
        (e8_0 > e21_0) and (rsi_now > RSI_BUY) and trend_up
    )
    sell_setup = (
        (c0 < o0) and (c0 < e21_0) and (close.iloc[-2] >= e21_0) and
        (e8_0 < e21_0) and (rsi_now < RSI_SELL) and trend_down
    )

    if buy_setup or sell_setup:
        direction = "BUY" if buy_setup else "SELL"
        sl_dist = max(atr_now * SL_ATR_MULT, 0.0020 if "XAUUSD" not in symbol else 2.0)
        tp_dist = atr_now * TP_ATR_MULT
        entry = c0
        sl = entry - sl_dist if buy_setup else entry + sl_dist
        tp = entry + tp_dist if buy_setup else entry - tp_dist
        lot = lot_size(entry, sl, symbol)

        msg = f"""SEÑAL {direction} {label}

Entrada: {entry:.5f}
SL: {sl:.5f}
TP: {tp:.5f}
Lote: {lot}
RSI: {rsi_now:.1f}
Tendencia: {'ALCISTA' if trend_up else 'BAJISTA'}"""

        send_email(f"{direction} {label}", msg)
        print("SEÑAL ENVIADA")

# MAIN
if __name__ == "__main__":
    print(f"=== Bot ajustado – más sensible para pullbacks ({datetime.now().strftime('%H:%M')}) ===")
    
    for i, (label, symbol) in enumerate(PARES):
        analyze(label, symbol)
        if i < 2:
            print(f"   Esperando 25 segundos...")
            time.sleep(25)
    
    print("\nCiclo terminado – ¡más señales en camino!")
