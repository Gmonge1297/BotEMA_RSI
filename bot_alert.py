import pandas as pd
import numpy as np
import yfinance as yf
import datetime as dt
import pytz
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials
import os

# ---------- Variables de entorno ----------
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

GOOGLE_SVC_JSON = os.getenv("GOOGLE_SVC_JSON")
GOOGLE_SHEET_NAME = "EMA_RSI_Signals"

# ---------- Parámetros de Estrategia ----------
CR_TZ = pytz.timezone("America/Costa_Rica")

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

SL_PIPS = 300
TP_PIPS = 600
MAX_RISK_USD = 1.5

TIMEFRAME_MINUTES = 60


pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

# ---------- Indicadores ----------
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(window=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series

# ---------- Envío de correo ----------
def send_email(subject, body):
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
        print("Correo enviado:", subject)
    except Exception as e:
        print("Error enviando correo:", e)

# ---------- Google Sheets ----------
def init_sheets():
    if not GOOGLE_SVC_JSON:
        print("No GOOGLE_SVC_JSON disponible.")
        return None

    creds_dict = json.loads(GOOGLE_SVC_JSON)
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    try:
        sh = client.open(GOOGLE_SHEET_NAME)
    except Exception:
        sh = client.create(GOOGLE_SHEET_NAME)

    ws = sh.sheet1
    headers = ["timestamp", "pair", "type", "entry", "sl", "tp",
               "rsi", "risk_usd", "result", "pips", "notes"]

    try:
        existing = ws.row_values(1)
    except:
        existing = []

    if existing != headers:
        ws.insert_row(headers, 1)

    return ws

def log_to_sheet(ws, row):
    if ws:
        try:
            ws.append_row(row)
        except Exception as e:
            print("Error guardando en Sheets:", e)

# ---------- Cálculos ----------
def pip_value(symbol):
    return 0.01 if "XAU" in symbol else 0.0001

def calculate_lot_for_risk(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)
    if sl_pips == 0:
        return 0.01
    value_per_pip_per_0_01 = 0.10
    lot = max_risk / (sl_pips * value_per_pip_per_0_01)
    return max(round(lot, 2), 0.01)

# ---------- Datos ----------
def fetch_ohlc_yf(symbol, period_minutes=60):
    try:
        df = yf.download(symbol, period="7d", interval=f"{period_minutes}m")
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "Open":"open",
            "High":"high",
            "Low":"low",
            "Close":"close",
            "Volume":"volume"
        })
        return df
    except Exception as e:
        print("Error al descargar datos:", e)
        return pd.DataFrame()

# ---------- Estrategia con Vela de Confirmación ----------
def analyze_pair(label, yf_symbol, ws=None):
    print(f"Descargando datos de {label}...")

    df = fetch_ohlc_yf(yf_symbol)
    if df.empty or len(df) < 60:
        print("Sin datos suficientes.")
        return

    close = df["close"]
    openv = df["open"]

    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    # velas prev_prev = -3, prev = -2, last = -1
    f2 = ema_fast.iat[-3]
    s2 = ema_slow.iat[-3]
    f1 = ema_fast.iat[-2]
    s1 = ema_slow.iat[-2]
    fl = ema_fast.iat[-1]
    sl = ema_slow.iat[-1]

    close1 = close.iat[-2]
    close_last = close.iat[-1]
    open_last = openv.iat[-1]

    rsi_last = rsi_series.iat[-1]

    # Cruces detectados en prev (-2)
    cross_up_prev = (f2 <= s2) and (f1 > s1)
    cross_dn_prev = (f2 >= s2) and (f1 < s1)

    # Confirmación estricta en last (-1)
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

    # ---- BUY ----
    if cross_up_prev and buy_confirm:
        entry = float(close_last)
        slv = entry - SL_PIPS * pip_value(yf_symbol)
        tpv = entry + TP_PIPS * pip_value(yf_symbol)
        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, yf_symbol)

        msg = f"""📈 Señal CONFIRMADA BUY {label}

Entrada: {entry}
SL: {slv}
TP: {tpv}
RSI: {rsi_last:.1f}
Riesgo: ${MAX_RISK_USD}
Lote sugerido: {lot}
"""

        send_email(f"BUY Confirmado {label}", msg)
        log_to_sheet(ws, [
            str(dt.datetime.now(CR_TZ)), label, "BUY",
            entry, slv, tpv, rsi_last, MAX_RISK_USD, "", "", "confirm"
        ])
        return

    # ---- SELL ----
    if cross_dn_prev and sell_confirm:
        entry = float(close_last)
        slv = entry + SL_PIPS * pip_value(yf_symbol)
        tpv = entry - TP_PIPS * pip_value(yf_symbol)
        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, yf_symbol)

        msg = f"""📉 Señal CONFIRMADA SELL {label}

Entrada: {entry}
SL: {slv}
TP: {tpv}
RSI: {rsi_last:.1f}
Riesgo: ${MAX_RISK_USD}
Lote sugerido: {lot}
"""

        send_email(f"SELL Confirmado {label}", msg)
        log_to_sheet(ws, [
            str(dt.datetime.now(CR_TZ)), label, "SELL",
            entry, slv, tpv, rsi_last, MAX_RISK_USD, "", "", "confirm"
        ])
        return


# ---------- Loop principal (CRON mode) ----------
if __name__ == "__main__":
    print("=== Bot ejecutándose (modo CRON) ===")
    ws = init_sheets()

    for label, symbol in pairs.items():
        analyze_pair(label, symbol, ws)

    print("No hubo señales esta hora.")
