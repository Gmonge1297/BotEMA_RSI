import yfinance as yf
import pandas as pd
import numpy as np
import datetime as dt
import pytz
import smtplib
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from email.mime.text import MIMEText

# ---------- CONFIGURACIÓN GENERAL ----------
CR_TZ = pytz.timezone("America/Costa_Rica")
MAX_RISK_USD = 10  # Riesgo fijo por operación
RISK_PER_TRADE = 0.01  # Riesgo 1%

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

# ---------- CONEXIÓN A GOOGLE SHEETS ----------
def init_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open("ForexBot_Log")
    return sheet.sheet1

def log_to_sheet(ws, row):
    ws.append_row(row)


# ---------- ENVÍO DE CORREOS ----------
def send_email(subject, body):
    sender = "TU_CORREO@gmail.com"
    app_password = "TU_CLAVE_DE_APLICACION"
    receiver = "TU_CORREO@gmail.com"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, app_password)
        smtp.send_message(msg)


# ---------- CÁLCULO DE INDICADORES ----------
def calc_indicators(df):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()

    delta = df["Close"].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(14).mean()
    avg_loss = pd.Series(loss).rolling(14).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    return df


# ---------- LÓGICA DE SEÑALES ----------
def analyze_pair(label, symbol, ws):
    try:
        data = yf.download(symbol, period="3d", interval="1h")
    except:
        print(f"Error al descargar datos para {label}")
        return False

    if len(data) < 60:
        print(f"Sin datos suficientes para {label}")
        return False

    df = calc_indicators(data)
    close = df["Close"].iloc[-1]
    ema20 = df["EMA20"].iloc[-1]
    ema50 = df["EMA50"].iloc[-1]
    rsi_last = df["RSI"].iloc[-1]

    prev_close = df["Close"].iloc[-2]
    prev_ema20 = df["EMA20"].iloc[-2]
    prev_ema50 = df["EMA50"].iloc[-2]

    # ======= REGLAS DE COMPRA (BUY) =======
    buy_signal = (
        prev_close < prev_ema20 and close > ema20 and  # ruptura confirmada
        ema20 > ema50 and                              # EMA20 encima de EMA50
        48 <= rsi_last <= 60                           # RSI saludable sin sobrecompra
    )

    # ======= REGLAS DE VENTA (SELL) =======
    sell_signal = (
        prev_close > prev_ema20 and close < ema20 and  # ruptura confirmada
        ema20 < ema50 and                              # EMA20 debajo de EMA50
        40 <= rsi_last <= 52                           # RSI bajista sin sobreventa
    )

    # ==== TP / SL AUTOMÁTICOS (1:2) ====
    if buy_signal:
        sl = close - (abs(close - ema20) * 2)
        tp = close + (abs(close - ema20) * 4)
        lot = round(MAX_RISK_USD / abs(close - sl), 2)

        msg = f"""
BUY Confirmado en {label}
Entrada: {close}
SL: {sl}
TP: {tp}
RSI: {rsi_last:.1f}
Riesgo: ${MAX_RISK_USD}
Lote sugerido: {lot}
"""
        send_email(f"BUY Confirmado {label}", msg)
        log_to_sheet(ws, [
            str(dt.datetime.now(CR_TZ)), label, "BUY",
            close, sl, tp, rsi_last, MAX_RISK_USD, lot, "", "confirm"
        ])
        return True

    if sell_signal:
        sl = close + (abs(close - ema20) * 2)
        tp = close - (abs(close - ema20) * 4)
        lot = round(MAX_RISK_USD / abs(close - sl), 2)

        msg = f"""
SELL Confirmado en {label}
Entrada: {close}
SL: {sl}
TP: {tp}
RSI: {rsi_last:.1f}
Riesgo: ${MAX_RISK_USD}
Lote sugerido: {lot}
"""
        send_email(f"SELL Confirmado {label}", msg)
        log_to_sheet(ws, [
            str(dt.datetime.now(CR_TZ)), label, "SELL",
            close, sl, tp, rsi_last, MAX_RISK_USD, lot, "", "confirm"
        ])
        return True

    return False  # No hubo señal para este par


# ---------- LOOP PRINCIPAL ----------
if __name__ == "__main__":
    print("=== Bot ejecutándose (modo CRON) ===")
    ws = init_sheets()

    signals_sent = False

    for label, symbol in pairs.items():
        if analyze_pair(label, symbol, ws):
            signals_sent = True

    if not signals_sent:
        print("No hubo señales esta hora.")
