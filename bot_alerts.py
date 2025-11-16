import pandas as pd
import numpy as np
import yfinance as yf
import datetime as dt
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials
import pytz
import os

# Cargar variables de entorno
from dotenv import load_dotenv
load_dotenv()

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

# ---------- Configuración general ----------
CR_TZ = pytz.timezone("America/Costa_Rica")

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14
RSI_BUY = 55
RSI_SELL = 45
SL_PIPS = 300
TP_PIPS = 600
MAX_RISK_USD = 15
CHECK_INTERVAL_SECONDS = 60
TIMEFRAME_MINUTES = 60
MINUTE_OFFSET = 0

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
GOOGLE_SVC_JSON = os.getenv("GOOGLE_SVC_JSON")
GOOGLE_SHEET_NAME = "EMA_RSI_Signals"

# ---------- Funciones de indicadores ----------
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
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_ADDRESS
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[{dt.datetime.now()}] Email enviado: {subject}")

    except Exception as e:
        print(f"[{dt.datetime.now()}] Error al enviar el correo: {e}")

# ---------- Google Sheets ----------
def init_sheets():
    if not GOOGLE_SVC_JSON:
        print("No GOOGLE_SVC_JSON disponible; no se guardarán entradas en Sheets.")
        return None

    creds_dict = json.loads(GOOGLE_SVC_JSON)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    try:
        sh = client.open(GOOGLE_SHEET_NAME)
    except Exception:
        sh = client.create(GOOGLE_SHEET_NAME)

    worksheet = sh.sheet1
    headers = ["timestamp", "par", "signal", "entry", "sl", "tp", "rsi", "risk_usd", "result", "pips", "notes"]
    existing = []
    try:
        existing = worksheet.row_values(1)
    except Exception:
        pass
    if existing != headers:
        worksheet.insert_row(headers, index=1)
    return worksheet

def log_to_sheet(ws, row):
    if not ws:
        return
    try:
        ws.append_row(row)
    except Exception as e:
        print("Error guardando en Sheets:", e)

# ---------- Cálculos de trading ----------
def pip_value(symbol):
    if "XAU" in symbol:
        return 0.01
    return 0.0001

def calculate_lot_for_risk(entry_price, sl_price, max_risk_usd, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry_price - sl_price) / pip)
    if sl_pips == 0:
        return 0.01
    value_per_pip_per_0_01 = 0.10
    lot = max_risk_usd / (sl_pips * value_per_pip_per_0_01)
    return round(max(lot, 0.01), 2)

# ---------- Datos de mercado ----------
def fetch_ohlc_yf(sym, period_minutes=60):
    try:
        ticker = yf.Ticker(sym)
        df = ticker.history(period="7d", interval=f"{period_minutes}m")
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:
        print("Error fetching data:", e)
        return pd.DataFrame()

# ---------- Lógica principal ----------
def analyze_pair(symbol_label, yf_symbol, ws=None):
    df = fetch_ohlc_yf(yf_symbol)
    if df.empty or len(df) < EMA_SLOW + RSI_PERIOD + 5:
        print(f"[{symbol_label}] Datos insuficientes")
        return None

    close = df["close"]
    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    price_close = close.iat[-1]
    ema_f_last, ema_s_last = ema_fast.iat[-1], ema_slow.iat[-1]
    ema_f_prev, ema_s_prev = ema_fast.iat[-2], ema_slow.iat[-2]
    rsi_last = rsi_series.iat[-1]

    # Señal de compra
    if (ema_f_prev <= ema_s_prev) and (ema_f_last > ema_s_last) and (rsi_last > RSI_BUY):
        entry = price_close
        sl = entry - SL_PIPS * pip_value(yf_symbol)
        tp = entry + TP_PIPS * pip_value(yf_symbol)
        lot = calculate_lot_for_risk(entry, sl, MAX_RISK_USD, yf_symbol)
        msg = f"✅ Señal COMPRA {symbol_label}\nEntrada: {entry}\nSL: {sl}\nTP: {tp}\nRSI: {rsi_last}"
        send_email(f"Señal COMPRA {symbol_label}", msg)
        log_to_sheet(ws, [str(dt.datetime.now(CR_TZ)), symbol_label, "BUY", entry, sl, tp, rsi_last, MAX_RISK_USD, "", "", ""])
        return

    # Señal de venta
    if (ema_f_prev >= ema_s_prev) and (ema_f_last < ema_s_last) and (rsi_last < RSI_SELL):
        entry = price_close
        sl = entry + SL_PIPS * pip_value(yf_symbol)
        tp = entry - TP_PIPS * pip_value(yf_symbol)
        lot = calculate_lot_for_risk(entry, sl, MAX_RISK_USD, yf_symbol)
        msg = f"⚠️ Señal VENTA {symbol_label}\nEntrada: {entry}\nSL: {sl}\nTP: {tp}\nRSI: {rsi_last}"
        send_email(f"Señal VENTA {symbol_label}", msg)
        log_to_sheet(ws, [str(dt.datetime.now(CR_TZ)), symbol_label, "SELL", entry, sl, tp, rsi_last, MAX_RISK_USD, "", "", ""])

def main_loop():
    print("Bot EMA+RSI iniciado")
    ws = None
    try:
        ws = init_sheets()
    except Exception as e:
        print("Error inicializando Sheets:", e)
        ws = None

    while True:
        for label, yf_symbol in pairs.items():
            analyze_pair(label, yf_symbol, ws)
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main_loop()
