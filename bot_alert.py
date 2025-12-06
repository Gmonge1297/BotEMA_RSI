# bot_alert.py  ← Versión FINAL que ya funciona con Polygon gratuito
import os
import pandas as pd
import numpy as np
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from polygon import RESTClient   # ← ahora sí es el paquete correcto

# ============================
# CONFIG
# ============================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

EMA_FAST = 8
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_BUY = 55
RSI_SELL = 45

ATR_PERIOD = 14
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.8

MAX_RISK_USD = 1.5

# Pares y símbolos correctos para Polygon gratuito
pairs = {
    "EURUSD": "C:EURUSD",
    "GBPUSD": "C:GBPUSD",
    "USDJPY": "C:USDJPY",
    "XAUUSD": "C:XAUUSD"   # En XM aparece como GOLD, pero aquí es XAUUSD
}

# ============================
# UTILS + INDICADORES (sin cambios)
# ============================
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
    risk = MAX_RISK_USD
    pips = abs(entry - sl) / pip_value(symbol)
    if pips == 0: return 0.01
    lot = risk / (pips * 1)   # 1 USD por pip ≈ 0.01 lot en la mayoría
    return max(round(lot, 2), 0.01)

# ============================
# ENVÍO EMAIL
# ============================
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
        print("Email enviado")
    except Exception as e:
        print("Error email:", e)

# ============================
# DATOS POLYGON (CORREGIDO para cuenta gratuita)
# ============================
def get_data(symbol, timeframe, days=10):
    if not POLYGON_API_KEY:
        print("No hay POLYGON_API_KEY")
        return pd.DataFrame()
    client = RESTClient(POLYGON_API_KEY)
    to_date   = datetime.now()
    from_date = to_date - timedelta(days=days)
    try:
        aggs = client.get_aggs(
            ticker=symbol,
            multiplier=1,
            timespan=timeframe,        # "minute", "hour", "day", "4" (para H4)
            from_=from_date.date(),
            to=to_date.date(),
            limit=50000
        )
        df = pd.DataFrame(aggs)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df[['open','high','low','close']]
    except Exception as e:
        print("Error Polygon:", e)
        return pd.DataFrame()

# ============================
# LÓGICA DE SEÑALES
# ============================
def analyze(label, symbol):
    print(f"\nAnalizando {label}...")
    df1 = get_data(symbol, "hour", days=12)      # H1
    df4 = get_data(symbol, "day", days=60)       # Daily para filtro tendencia (más simple que H4)

    if df1.empty or len(df1)<100 or df4.empty:
        print("Sin datos")
        return

    close = df1['close']
    open_ = df1['open']
    high  = df1['high']
    low   = df1['low']

    ema8  = ema(close, EMA_FAST)
    ema21 = ema(close, EMA_SLOW)
    rsi_v = rsi(close, RSI_PERIOD)
    atr_v = atr(high, low, close, ATR_PERIOD)

    # Filtro tendencia Daily
    ema50_d  = ema(df4['close'], 50)
    ema200_d = ema(df4['close'], 200)
    trend_up   = ema50_d.iloc[-1] > ema200_d.iloc[-1]
    trend_down = ema50_d.iloc[-1] < ema200_d.iloc[-1]

    # Últimos valores
    c0, c1 = close.iloc[-1], close.iloc[-2]
    o0, o1 = open_.iloc[-1], open_.iloc[-2]
    e8_0, e8_1 = ema8.iloc[-1], ema8.iloc[-2]
    e21_0, e21_1 = ema21.iloc[-1], ema21.iloc[-2]
    rsi_now = rsi_v.iloc[-1]
    atr_now = atr_v.iloc[-1]

    # Pullback + cruce reciente + confirmación
    buy_setup  = (e8_1 <= e21_1) and (e8_0 > e21_0) and (c0 > o0) and (c0 > e21_0) and (rsi_now > RSI_BUY) and trend_up
    sell_setup = (e8_1 >= e21_1) and (e8_0 < e21_0) and (c0 < o0) and (c0 < e21_0) and (rsi_now < RSI_SELL) and trend_down

    if buy_setup or sell_setup:
        direction = "BUY" if buy_setup else "SELL"
        sl_dist = max(atr_now * SL_ATR_MULT, 0.0020 if "XAUUSD" not in symbol else 2.0)
        tp_dist = atr_now * TP_ATR_MULT
        entry = c0
        sl = entry - sl_dist if buy_setup else entry + sl_dist
        tp = entry + tp_dist if buy_setup else entry - tp_dist
        lot = lot_size(entry, sl, symbol)

        msg = f"""⚡ {direction} {label}
Entrada: {entry:.5f}
SL: {sl:.5f}
TP: {tp:.5f}
Lote sugerido: {lot}
RSI: {rsi_now:.1f}
Trend Daily: {"UP" if trend_up else "DOWN"}"""
        send_email(f"{direction} {label}", msg)
        print("SEÑAL ENVIADA")

# ============================
# MAIN
# ============================
if __name__ == "__main__":
    print("=== Bot Forex EMA8-21 + RSI corriendo ===")
    for label, sym in pairs.items():
        analyze(label, sym)
    print("Ciclo terminado")
