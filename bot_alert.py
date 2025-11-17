import pandas as pd
import numpy as np
import datetime as dt
import pytz
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from alpha_vantage.foreignexchange import ForeignExchange
import os


# ===============================
# CONFIGURACIÓN
# ===============================

CR_TZ = pytz.timezone("America/Costa_Rica")

# AlphaVantage API Key (desde Secrets de GitHub)
ALPHA_KEY = os.getenv("ALPHAVANTAGE_KEY")

# Configuración de email
EMAIL_USER = "gmonge.botfx@gmail.com"
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = "edgardoms2010@gmail.com"

# Estrategia
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

SL_PIPS = 300
TP_PIPS = 600
MAX_RISK_USD = 1.5

# Pares
pairs = {
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "XAUUSD": "XAU/USD"
}


# ===============================
# FUNCIONES DE INDICADORES
# ===============================

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    avg_gain = pd.Series(gain).rolling(window=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ===============================
# ENVÍO DE EMAIL
# ===============================

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        server.quit()

        print("Correo enviado:", subject)
    except Exception as e:
        print("Error enviando correo:", e)


# ===============================
# DESCARGAR DATOS ALPHAVANTAGE
# ===============================

def fetch_alpha(symbol):
    try:
        fx = ForeignExchange(key=ALPHA_KEY, output_format="pandas")
        data, _ = fx.get_currency_exchange_intraday(
            from_symbol=symbol.split("/")[0],
            to_symbol=symbol.split("/")[1],
            interval="60min",
            outputsize="full"
        )
        df = data.sort_index()
        df = df.rename(columns={
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close"
        })
        return df
    except Exception as e:
        print("Error AlphaVantage:", e)
        return pd.DataFrame()


# ===============================
# VALORES DE PIP Y RISK
# ===============================

def pip_value(symbol):
    if "JPY" in symbol:
        return 0.01
    if "XAU" in symbol:
        return 0.1
    return 0.0001


def calculate_lot(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)
    if sl_pips == 0:
        return 0.01
    value_per_pip_0_01 = 0.10
    lot = max_risk / (sl_pips * value_per_pip_0_01)
    return max(round(lot, 2), 0.01)


# ===============================
# ANÁLISIS DE CADA PAR
# ===============================

def analyze_pair(label, symbol):
    print(f"Descargando datos de {label}...")

    df = fetch_alpha(symbol)

    if df.empty or len(df) < 60:
        print("Sin datos suficientes.\n")
        return

    close = df["close"]
    openv = df["open"]

    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    # Velas prev (-2) y last (-1)
    f2 = ema_fast.iloc[-3]
    s2 = ema_slow.iloc[-3]
    f1 = ema_fast.iloc[-2]
    s1 = ema_slow.iloc[-2]
    fl = ema_fast.iloc[-1]
    sl = ema_slow.iloc[-1]

    close_last = close.iloc[-1]
    open_last = openv.iloc[-1]
    rsi_last = rsi_series.iloc[-1]

    cross_up_prev = (f2 <= s2) and (f1 > s1)
    cross_dn_prev = (f2 >= s2) and (f1 < s1)

    buy_confirm = (
        close_last > open_last and
        close_last > fl and close_last > sl and
        rsi_last > RSI_BUY
    )

    sell_confirm = (
        close_last < open_last and
        close_last < fl and close_last < sl and
        rsi_last < RSI_SELL
    )

    # BUY
    if cross_up_prev and buy_confirm:
        entry = float(close_last)
        slv = entry - SL_PIPS * pip_value(label)
        tpv = entry + TP_PIPS * pip_value(label)
        lot = calculate_lot(entry, slv, MAX_RISK_USD, label)

        msg = f"""
📈 Señal BUY CONFIRMADA {label}

Entrada: {entry}
Stop Loss: {slv}
Take Profit: {tpv}
RSI: {rsi_last:.1f}
Lote sugerido: {lot}
Riesgo: ${MAX_RISK_USD}
"""
        send_email(f"BUY Confirmado {label}", msg)
        return

    # SELL
    if cross_dn_prev and sell_confirm:
        entry = float(close_last)
        slv = entry + SL_PIPS * pip_value(label)
        tpv = entry - TP_PIPS * pip_value(label)
        lot = calculate_lot(entry, slv, MAX_RISK_USD, label)

        msg = f"""
📉 Señal SELL CONFIRMADA {label}

Entrada: {entry}
Stop Loss: {slv}
Take Profit: {tpv}
RSI: {rsi_last:.1f}
Lote sugerido: {lot}
Riesgo: ${MAX_RISK_USD}
"""
        send_email(f"SELL Confirmado {label}", msg)
        return


# ===============================
# LOOP PRINCIPAL
# ===============================

if __name__ == "__main__":
    print("=== Bot ejecutándose (AlphaVantage) ===\n")

    for label, symbol in pairs.items():
        analyze_pair(label, symbol)
        time.sleep(1)

    print("No hubo señales esta hora.\n")
