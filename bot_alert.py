#!/usr/bin/env python3
# coding: utf-8
"""
Bot EMA20/EMA50 + RSI + vela confirmatoria (yfinance)
Diseñado para ejecutarse por cron / GitHub Actions una vez por ejecución (ej: cada hora).
Variables de entorno necesarias:
  - EMAIL_USER
  - EMAIL_PASSWORD
  - EMAIL_TO
Opcionales (envs):
  - ACCOUNT_BALANCE  (USD, para calcular $ riesgo en base a RISK_PERCENT)
  - RISK_PERCENT     (por defecto 1.0)
  - PAUSE_BETWEEN_PAIRS (segundos, default 2)
  - YF_MAX_RETRIES   (int, default 2) -- reintentos ante fallas de descarga
"""

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

# -------------------------
# CONFIG (ajustables)
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

# RSI flexible bands (intermedio)
RSI_BUY_MIN = 50
RSI_BUY_MAX = 65
RSI_SELL_MIN = 35
RSI_SELL_MAX = 50

# Cross must have happened within last N candles
CROSS_LOOKBACK = 5

# Candle confirmation uses last closed candle offset (use -1 or -2 depending when cron runs)
LAST_CANDLE_OFFSET = -1

# SL/TP settings (pips)
SL_PIPS = 300
TP_PIPS = 600
# for swing SL we will base on last N candles
SWING_LOOKBACK = 5
SL_BUFFER_PIPS = 5  # buffer beyond the swing

# Risk management
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))  # percent of account per trade
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", "0") or 0.0)  # if 0 will use MAX_RISK_USD
MAX_RISK_USD = float(os.getenv("MAX_RISK_USD", "0") or 0.0)  # fallback flat USD risk

# Pauses & retries
PAUSE_BETWEEN_PAIRS = float(os.getenv("PAUSE_BETWEEN_PAIRS", "2"))
YF_MAX_RETRIES = int(os.getenv("YF_MAX_RETRIES", "2"))

# Email envs (required)
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER)

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Misc
DEEP_LOG = False  # set True to print traceback on exceptions (helpful while debugging)


# -------------------------
# Helpers: indicators & utils
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
    """
    Normalize yfinance DataFrame: flatten MultiIndex columns if present,
    lowercase, and ensure 'close','open','high','low' exist if possible.
    Returns empty df if can't find close.
    """
    if isinstance(df.columns, pd.MultiIndex):
        cols = []
        for col in df.columns.values:
            # join non-empty parts with underscore
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
    """
    Approx pip value for price differences (not money per pip).
    Adjust to your broker if needed:
    - JPY pairs -> 0.01
    - Gold -> 0.01 (we use 0.01 to be conservative)
    - Others -> 0.0001
    """
    sym = symbol.upper()
    if "JPY" in sym:
        return 0.01
    if "GC=" in sym or "XAU" in sym:
        return 0.01
    return 0.0001

def calculate_lot_for_risk(entry_price: float, sl_price: float, max_risk_usd: float, symbol: str) -> float:
    """
    Very rough lot estimator:
    assume micro-lot 0.01 -> approx $0.10 per pip (varies greatly).
    This returns lot in increments of 0.01.
    """
    pip = pip_value(symbol)
    sl_pips = abs((entry_price - sl_price) / pip) if pip != 0 else 0
    if sl_pips == 0 or max_risk_usd <= 0:
        return 0.01
    value_per_pip_per_0_01 = 0.10
    lot = max_risk_usd / (sl_pips * value_per_pip_per_0_01)
    lot = max(lot, 0.01)
    return round(lot, 2)

def get_max_risk_usd() -> float:
    """
    Determine USD risk per trade: priority:
    - if ACCOUNT_BALANCE > 0 and RISK_PERCENT > 0 -> compute
    - elif MAX_RISK_USD env provided -> use it
    - else fallback small number
    """
    if ACCOUNT_BALANCE > 0 and RISK_PERCENT > 0:
        return ACCOUNT_BALANCE * (RISK_PERCENT / 100.0)
    if MAX_RISK_USD > 0:
        return MAX_RISK_USD
    return 1.0  # minimal fallback

# -------------------------
# yfinance download with retries
# -------------------------
def fetch_ohlc(yf_symbol: str, interval: str = "1h", period: str = "7d"):
    for attempt in range(1, YF_MAX_RETRIES + 2):
        try:
            df = yf.download(yf_symbol, interval=interval, period=period, progress=False)
            return df
        except Exception as e:
            print(f"Warning: yfinance download failed (attempt {attempt}): {e}")
            if attempt <= YF_MAX_RETRIES:
                time.sleep(1 + attempt)
                continue
            return pd.DataFrame()
    return pd.DataFrame()

# -------------------------
# Strategy helpers: detect cross within last N candles
# -------------------------
def cross_within(ema_fast: pd.Series, ema_slow: pd.Series, lookback: int = 5):
    """
    Return True if there was a cross (fast crossing slow) in the last `lookback` candles.
    We detect cross up (fast from <= slow to > slow) or cross down.
    Returns tuple (cross_up, cross_down).
    """
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return False, False
    start = max(1, len(ema_fast) - lookback)
    cross_up = False
    cross_down = False
    for i in range(start, len(ema_fast)):
        prev_f = ema_fast.iat[i-1]
        prev_s = ema_slow.iat[i-1]
        cur_f = ema_fast.iat[i]
        cur_s = ema_slow.iat[i]
        if (prev_f <= prev_s) and (cur_f > cur_s):
            cross_up = True
        if (prev_f >= prev_s) and (cur_f < cur_s):
            cross_down = True
    return cross_up, cross_down

def calc_swing_sl(symbol: str, low_series: pd.Series, high_series: pd.Series, is_buy: bool, lookback: int = 5, buffer_pips: int = SL_BUFFER_PIPS):
    """
    For a BUY: SL is slightly below the local swing low of last lookback bars.
    For a SELL: SL is slightly above the local swing high.
    Returns sl_price (float).
    """
    pip = pip_value(symbol)
    if is_buy:
        swing_low = float(low_series.tail(lookback).min())
        sl = swing_low - buffer_pips * pip
    else:
        swing_high = float(high_series.tail(lookback).max())
        sl = swing_high + buffer_pips * pip
    return sl

# -------------------------
# Signal analysis per pair
# -------------------------
def analyze_pair(label: str, yf_symbol: str):
    now = dt.datetime.now(CR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] Descargando datos de {label} ({yf_symbol})...")
    df_raw = fetch_ohlc(yf_symbol, interval="1h", period="7d")
    if df_raw.empty:
        print(f"Sin datos para {label} (df empty).")
        return None

    df = normalize_yf_df(df_raw)
    if df.empty or "close" not in df.columns:
        print(f"❌ No existe columna close para {label}. Omitiendo.")
        return None

    if len(df) < (EMA_SLOW + RSI_PERIOD + 2):
        print(f"Sin suficientes velas para {label} ({len(df)}).")
        return None

    close = df["close"].astype(float)
    low = df.get("low", close).astype(float)
    high = df.get("high", close).astype(float)
    openv = df.get("open", close).astype(float)

    ema_f = ema(close, EMA_FAST)
    ema_s = ema(close, EMA_SLOW)
    rsi_s = rsi(close, RSI_PERIOD)

    # detect cross in lookback
    cross_up, cross_down = cross_within(ema_f, ema_s, CROSS_LOOKBACK)

    # use last closed candle offset
    last = LAST_CANDLE_OFFSET
    prev = LAST_CANDLE_OFFSET - 1

    try:
        price_close = float(close.iat[last])
        price_open_last = float(openv.iat[last]) if "open" in df.columns else price_close
        ema_f_last = float(ema_f.iat[last])
        ema_s_last = float(ema_s.iat[last])
        ema_f_prev = float(ema_f.iat[prev])
        ema_s_prev = float(ema_s.iat[prev])
        rsi_last = float(rsi_s.iat[last])
    except Exception as e:
        if DEEP_LOG:
            traceback.print_exc()
        print("No se pudo tomar datos de índices finales; omitiendo par.")
        return None

    # Candle confirmation: direction and relative position to EMAs
    candle_bull = price_close > price_open_last
    candle_bear = price_close < price_open_last

    buy_candidate = False
    sell_candidate = False

    # Conditions for buy: recent cross up + candle confirmation + rsi in band + price above EMAs
    if cross_up:
        if (price_close > ema_f_last) and (price_close > ema_s_last) and candle_bull:
            if (RSI_BUY_MIN <= rsi_last <= RSI_BUY_MAX):
                buy_candidate = True

    # Conditions for sell
    if cross_down:
        if (price_close < ema_f_last) and (price_close < ema_s_last) and candle_bear:
            if (RSI_SELL_MIN <= rsi_last <= RSI_SELL_MAX):
                sell_candidate = True

    if not (buy_candidate or sell_candidate):
        return None

    # compute SL via swing and TP via RR=1:2
    is_buy = buy_candidate
    sl_price = calc_swing_sl(yf_symbol, low, high, is_buy, SWING_LOOKBACK, SL_BUFFER_PIPS)
    if is_buy:
        entry_price = price_close
        sl = sl_price
        tp = entry_price + 2 * abs(entry_price - sl)
    else:
        entry_price = price_close
        sl = sl_price
        tp = entry_price - 2 * abs(entry_price - sl)

    # max risk in USD
    max_risk_usd = get_max_risk_usd()
    lot = calculate_lot_for_risk(entry_price, sl, max_risk_usd, yf_symbol)

    sig = {
        "pair": label,
        "type": "BUY" if is_buy else "SELL",
        "entry": float(entry_price),
        "sl": float(sl),
        "tp": float(tp),
        "rsi": float(rsi_last),
        "lot": lot,
        "max_risk_usd": max_risk_usd
    }
    return sig

# -------------------------
# Email helpers
# -------------------------
def send_email(subject: str, html_body: str):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html_body, "html"))
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print(f"📧 Email enviado: {subject}")
        return True
    except Exception as e:
        print("❌ Error enviando email:", e)
        if DEEP_LOG:
            traceback.print_exc()
        return False

def build_html_message(sig: dict):
    return f"""
    <html>
      <body>
        <h2>Señal confirmada — {sig['pair']} ({sig['type']})</h2>
        <ul>
          <li><b>Entrada:</b> {sig['entry']:.5f}</li>
          <li><b>Stop Loss:</b> {sig['sl']:.5f}</li>
          <li><b>Take Profit:</b> {sig['tp']:.5f}</li>
          <li><b>RSI:</b> {sig['rsi']:.1f}</li>
          <li><b>Lote sugerido:</b> {sig['lot']}</li>
          <li><b>Riesgo por trade (USD aprox):</b> {sig['max_risk_usd']:.2f}</li>
        </ul>
        <p>Bot: EMA20/EMA50 + RSI (flex) + vela confirmatoria</p>
        <small>Generado: {dt.datetime.now(CR_TZ).strftime('%Y-%m-%d %H:%M:%S')}</small>
      </body>
    </html>
    """

# -------------------------
# MAIN (one-shot run)
# -------------------------
def main():
    print("=== Bot Intermedio: EMA20/EMA50 + RSI + Vela confirmatoria ===")
    any_signal = False
    for label, sym in PAIRS.items():
        try:
            sig = analyze_pair(label, sym)
            # respect pause to reduce rate-limits
            time.sleep(PAUSE_BETWEEN_PAIRS)
            if sig:
                any_signal = True
                subj = f"Señal {sig['type']} {sig['pair']} — EMA+RSI Confirmada"
                html = build_html_message(sig)
                send_email(subj, html)
                print(f"Señal encontrada: {sig['pair']} {sig['type']} (entrada {sig['entry']:.5f})")
            else:
                print(f"— No hubo señal para {label}")
        except Exception as e:
            print(f"Error procesando {label}: {e}")
            if DEEP_LOG:
                traceback.print_exc()

    if not any_signal:
        print("No hubo señales en esta ejecución.")
    print("=== Fin ejecución ===")

if __name__ == "__main__":
    main()
