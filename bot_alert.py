#!/usr/bin/env python3
# coding: utf-8

import os
import time
import math
import traceback
import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pytz
import datetime as dt

# === Import nuevo módulo de salida inteligente ===
from exit_rules import check_exit_signal


# -------------------------
# CONFIG
# -------------------------
CR_TZ = pytz.timezone("America/Costa_Rica")

PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY_MIN = 50
RSI_BUY_MAX = 65
RSI_SELL_MIN = 35
RSI_SELL_MAX = 50

CROSS_LOOKBACK = 5
LAST_CANDLE_OFFSET = -1

SL_PIPS = 300
TP_PIPS = 600
SWING_LOOKBACK = 5
SL_BUFFER_PIPS = 5

RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "0") or 0.0)
MAX_RISK_USD = float(os.getenv("MAX_RISK_USD", "0") or 0.0)

PAUSE_BETWEEN_PAIRS = float(os.getenv("PAUSE_BETWEEN_PAIRS", "2"))
YF_MAX_RETRIES = int(os.getenv("YF_MAX_RETRIES", "2"))

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER)

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

DEEP_LOG = False


# -------------------------
# Helpers
# -------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1/period, adjust=False).mean()
    avg_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_up / avg_down
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series

def normalize_yf_df(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        cols = []
        for col in df.columns.values:
            cols.append("_".join([str(p) for p in col if p and str(p).strip()]))
        df.columns = cols
    df.columns = [c.lower() for c in df.columns]

    close_candidates = [c for c in df.columns if "close" in c]
    if not close_candidates:
        return pd.DataFrame()

    df["close"] = df[close_candidates[0]]
    for key in ("open", "high", "low"):
        cand = [c for c in df.columns if key in c]
        if cand:
            df[key] = df[cand[0]]
    return df

def pip_value(symbol: str) -> float:
    sym = symbol.upper()
    if "JPY" in sym:
        return 0.01
    if "GC=" in sym or "XAU" in sym:
        return 0.01
    return 0.0001

def calculate_lot_for_risk(entry_price: float, sl_price: float, max_risk_usd: float, symbol: str) -> float:
    pip = pip_value(symbol)
    sl_pips = abs((entry_price - sl_price) / pip) if pip != 0 else 0
    if sl_pips == 0 or max_risk_usd <= 0:
        return 0.01
    value_per_pip_per_0_01 = 0.10
    lot = max_risk_usd / (sl_pips * value_per_pip_per_0_01)
    lot = max(lot, 0.01)
    return round(lot, 2)

def get_max_risk_usd() -> float:
    if ACCOUNT_BALANCE > 0 and RISK_PERCENT > 0:
        return ACCOUNT_BALANCE * (RISK_PERCENT / 100.0)
    if MAX_RISK_USD > 0:
        return MAX_RISK_USD
    return 1.0


# -------------------------
# yfinance
# -------------------------
def fetch_ohlc(yf_symbol: str, interval="1h", period="7d"):
    for attempt in range(1, YF_MAX_RETRIES + 2):
        try:
            df = yf.download(yf_symbol, interval=interval, period=period, progress=False)
            return df
        except:
            time.sleep(1 + attempt)
            continue
    return pd.DataFrame()


# -------------------------
# Cross detection
# -------------------------
def cross_within(ema_fast, ema_slow, lookback=5):
    if len(ema_fast) < 2:
        return False, False
    start = max(1, len(ema_fast) - lookback)
    cross_up = cross_down = False

    for i in range(start, len(ema_fast)):
        if ema_fast.iat[i-1] <= ema_slow.iat[i-1] and ema_fast.iat[i] > ema_slow.iat[i]:
            cross_up = True
        if ema_fast.iat[i-1] >= ema_slow.iat[i-1] and ema_fast.iat[i] < ema_slow.iat[i]:
            cross_down = True
    return cross_up, cross_down


# -------------------------
# Swing Stop Loss
# -------------------------
def calc_swing_sl(symbol, low_series, high_series, is_buy, lookback=5, buffer_pips=SL_BUFFER_PIPS):
    pip = pip_value(symbol)
    if is_buy:
        swing_low = float(low_series.tail(lookback).min())
        sl = swing_low - buffer_pips * pip
    else:
        swing_high = float(high_series.tail(lookback).max())
        sl = swing_high + buffer_pips * pip
    return sl


# -------------------------
# ANALIZA PAR (entrada)
# -------------------------
def analyze_pair(label, yf_symbol):
    now = dt.datetime.now(CR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Descargando datos de {label} ({yf_symbol})...")

    df_raw = fetch_ohlc(yf_symbol)
    if df_raw.empty:
        return None

    df = normalize_yf_df(df_raw)
    if df.empty or "close" not in df.columns:
        return None

    if len(df) < (EMA_SLOW + RSI_PERIOD + 2):
        return None

    close = df["close"].astype(float)
    low = df.get("low", close).astype(float)
    high = df.get("high", close).astype(float)
    openv = df.get("open", close).astype(float)

    ema_f = ema(close, EMA_FAST)
    ema_s = ema(close, EMA_SLOW)
    rsi_s = rsi(close, RSI_PERIOD)

    cross_up, cross_down = cross_within(ema_f, ema_s, CROSS_LOOKBACK)

    last = LAST_CANDLE_OFFSET
    prev = LAST_CANDLE_OFFSET - 1

    try:
        price_close = float(close.iat[last])
        price_open_last = float(openv.iat[last])
        ema_f_last = float(ema_f.iat[last])
        ema_s_last = float(ema_s.iat[last])
        rsi_last = float(rsi_s.iat[last])
    except:
        return None

    candle_bull = price_close > price_open_last
    candle_bear = price_close < price_open_last

    buy = sell = False

    if cross_up and candle_bull and price_close > ema_f_last and price_close > ema_s_last:
        if RSI_BUY_MIN <= rsi_last <= RSI_BUY_MAX:
            buy = True

    if cross_down and candle_bear and price_close < ema_f_last and price_close < ema_s_last:
        if RSI_SELL_MIN <= rsi_last <= RSI_SELL_MAX:
            sell = True

    if not (buy or sell):
        return None

    is_buy = buy
    sl = calc_swing_sl(yf_symbol, low, high, is_buy, SWING_LOOKBACK, SL_BUFFER_PIPS)

    entry = price_close

    if is_buy:
        tp = entry + 2 * abs(entry - sl)
    else:
        tp = entry - 2 * abs(entry - sl)

    max_risk_usd = get_max_risk_usd()
    lot = calculate_lot_for_risk(entry, sl, max_risk_usd, yf_symbol)

    return {
        "pair": label,
        "type": "BUY" if is_buy else "SELL",
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "rsi": float(rsi_last),
        "lot": lot,
        "max_risk_usd": max_risk_usd
    }


# -------------------------
# EMAIL
# -------------------------
def send_email(subject, html_body):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html_body, "html"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print(f"📧 Email enviado: {subject}")
        return True
    except Exception as e:
        print("❌ Error enviando email:", e)
        return False


def build_html_message(sig):
    return f"""
    <html>
      <body>
        <h2>Señal confirmada — {sig['pair']} ({sig['type']})</h2>
        <ul>
          <li><b>Entrada:</b> {sig['entry']:.5ف}</li>
          <li><b>Stop Loss:</b> {sig['sl']:.5ف}</li>
          <li><b>Take Profit:</b> {sig['tp']:.5ف}</li>
          <li><b>RSI:</b> {sig['rsi']:.1f}</li>
          <li><b>Lote sugerido:</b> {sig['lot']}</li>
          <li><b>Riesgo por trade (USD):</b> {sig['max_risk_usd']:.2f}</li>
        </ul>
        <p>Bot: EMA20/EMA50 + RSI + Salida Inteligente</p>
      </body>
    </html>
    """


# -------------------------
# MAIN
# -------------------------
def main():
    print("=== Bot Intermedio: EMA20/EMA50 + RSI + Salida Inteligente ===")

    # ========== PARTE 1 — detectar SALIDA inteligente ==========
    try:
        with open("last_trade.txt", "r") as f:
            data = f.read().strip().split(",")
            last_pair, last_type, last_entry = data[0], data[1], float(data[2])
    except:
        last_pair = None

    if last_pair:
        yf_symbol = PAIRS.get(last_pair)
        df_exit = fetch_ohlc(yf_symbol, interval="1h", period="5d")
        df_exit = normalize_yf_df(df_exit)

        if not df_exit.empty:
            if check_exit_signal(df_exit, last_entry, last_type):
                price_now = float(df_exit["close"].iloc[-1])
                html = f"""
                <html><body>
                <h2>Salida recomendada — {last_pair}</h2>
                <p>Se detecta reversa, y estás en profit.</p>
                <ul>
                    <li>Dirección: {last_type}</li>
                    <li>Entrada: {last_entry}</li>
                    <li>Precio actual: {price_now}</li>
                </ul>
                <p><b>Recomendación:</b> considerar cierre parcial o total.</p>
                </body></html>
                """
                send_email(f"Salida recomendada — {last_pair}", html)

    # ========== PARTE 2 — detectar ENTRADAS ==========
    for label, sym in PAIRS.items():
        try:
            sig = analyze_pair(label, sym)
            time.sleep(PAUSE_BETWEEN_PAIRS)
            if sig:
                # Guardar última operación
                with open("last_trade.txt", "w") as f:
                    f.write(f"{sig['pair']},{sig['type']},{sig['entry']}")

                subj = f"Señal {sig['type']} {sig['pair']} — Confirmada"
                html = build_html_message(sig)
                send_email(subj, html)
                print(f"Señal encontrada: {sig['pair']} {sig['type']}")

            else:
                print(f"— No hubo señal para {label}")

        except Exception as e:
            print(f"Error con {label}: {e}")

    print("=== Fin ejecución ===")


if __name__ == "__main__":
    main()
