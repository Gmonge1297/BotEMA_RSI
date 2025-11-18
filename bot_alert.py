#!/usr/bin/env python3
# coding: utf-8
"""
Bot de alertas EMA20/EMA50 + RSI + vela de confirmación (yfinance).
Diseñado para ejecutarse por cron / GitHub Actions (una pasada).
Variables de entorno necesarias:
  - EMAIL_USER        : cuenta SMTP (ej. gmonge.botfx@gmail.com)
  - EMAIL_PASSWORD    : app password o contraseña SMTP
  - EMAIL_TO          : destinatario de las alertas
Opcionales:
  - MAX_RISK_USD      : riesgo por trade en USD (default 1.5)
"""

import os
import time
import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pytz
import datetime as dt

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
RSI_BUY = 55    # umbral para considerar fuerza alcista en confirmación
RSI_SELL = 45   # umbral para considerar fuerza bajista en confirmación

# pips (usados para SL/TP como número de pips)
SL_PIPS = 300
TP_PIPS = 600

# riesgo por operación en USD (puedes sobreescribir por env)
MAX_RISK_USD = float(os.getenv("MAX_RISK_USD", "1.5"))

DELAY_BETWEEN_PAIRS = 2  # segundos pause entre descargas para mitigar rate-limit

# Email envs
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER)

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# -------------------------
# Helpers indicadores
# -------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    # Wilder smoothing (EMA of gains/losses)
    avg_up = up.ewm(alpha=1/period, adjust=False).mean()
    avg_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_up / avg_down
    return 100 - (100 / (1 + rs))

# -------------------------
# Market utilities
# -------------------------
def normalize_yf_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplana MultiIndex si existe, pasa a minúsculas, y asegura columna 'close'.
    """
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['_'.join([str(p) for p in col if p]).strip() for col in df.columns.values]
    # bajar a minúsculas
    df.columns = [c.lower() for c in df.columns]
    # buscar candidato close
    close_candidates = [c for c in df.columns if "close" in c]
    if not close_candidates:
        return pd.DataFrame()  # señal de que no hay close disponible
    # normalizamos a 'close'
    df["close"] = df[close_candidates[0]]
    # normalizar open/high/low if possible
    open_candidates = [c for c in df.columns if "open" in c]
    high_candidates = [c for c in df.columns if "high" in c]
    low_candidates  = [c for c in df.columns if "low" in c]
    if open_candidates:
        df["open"] = df[open_candidates[0]]
    if high_candidates:
        df["high"] = df[high_candidates[0]]
    if low_candidates:
        df["low"] = df[low_candidates[0]]
    return df

def pip_value(symbol: str) -> float:
    """Valor del pip según símbolo (aprox.)."""
    if "JPY" in symbol or "JPY=" in symbol:
        return 0.01
    if "XAU" in symbol or "GC=" in symbol:
        return 0.01  # puedes ajustar según bróker (0.01 o 0.1)
    return 0.0001

def calculate_lot_for_risk(entry_price: float, sl_price: float, max_risk_usd: float, symbol: str) -> float:
    pip = pip_value(symbol)
    sl_pips = abs((entry_price - sl_price) / pip)
    if sl_pips == 0:
        return 0.01
    # asumimos micro-lote 0.01 -> $0.10 / pip approx (EURUSD)
    value_per_pip_per_0_01 = 0.10
    lot = max_risk_usd / (sl_pips * value_per_pip_per_0_01)
    lot = max(lot, 0.01)
    # round to 2 decimals to support 0.01 micro-lots
    return round(lot, 2)

# -------------------------
# Señal logic
# -------------------------
def analyze_pair(label: str, yf_symbol: str):
    print(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {label} ({yf_symbol})...")
    try:
        # descarga H1 7 días (suficiente para cálculo)
        df = yf.download(yf_symbol, interval="1h", period="7d", progress=False)
    except Exception as e:
        print("Error descargando datos:", e)
        return None

    if df.empty:
        print("Sin datos (df.empty).")
        return None

    df = normalize_yf_df(df)
    if df.empty or "close" not in df.columns:
        print("❌ No existe columna close en los datos. Se omite.")
        return None

    # necesitamos al menos (EMA_SLOW + RSI_PERIOD + 2) velas para cálculos conservadores
    if len(df) < (EMA_SLOW + RSI_PERIOD + 2):
        print("Sin datos suficientes (pocas velas).")
        return None

    close = df["close"].astype(float)
    # indicadores
    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    # índices: last closed candle = -2 (si cron ejecuta justo después de cierre),
    # pero para seguridad usaremos last = -1 (última disponible) como confirmación de vela completa
    # previous candle = -2
    last_idx = -1
    prev_idx = -2

    price_close = float(close.iat[last_idx])
    ema_f_last = float(ema_fast.iat[last_idx])
    ema_s_last = float(ema_slow.iat[last_idx])
    ema_f_prev = float(ema_fast.iat[prev_idx])
    ema_s_prev = float(ema_slow.iat[prev_idx])
    rsi_last = float(rsi_series.iat[last_idx])

    # candle info
    open_col = df.get("open", None)
    if open_col is not None:
        open_last = float(open_col.iat[last_idx])
    else:
        open_last = price_close  # fallback (rare)

    # Confirmaciones:
    # cross happened between prev and last
    cross_up = (ema_f_prev <= ema_s_prev) and (ema_f_last > ema_s_last)
    cross_down = (ema_f_prev >= ema_s_prev) and (ema_f_last < ema_s_last)

    # strict candle confirmation: last candle close relative to EMAs and candle direction
    buy_confirm = (cross_up and (price_close > ema_f_last) and (price_close > ema_s_last)
                   and (price_close > open_last) and (rsi_last > RSI_BUY))
    sell_confirm = (cross_down and (price_close < ema_f_last) and (price_close < ema_s_last)
                    and (price_close < open_last) and (rsi_last < RSI_SELL))

    if buy_confirm:
        entry = price_close
        sl = entry - SL_PIPS * pip_value(yf_symbol)
        tp = entry + TP_PIPS * pip_value(yf_symbol)
        lot = calculate_lot_for_risk(entry, sl, MAX_RISK_USD, yf_symbol)
        return {
            "type": "BUY",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rsi": rsi_last,
            "lot": lot
        }

    if sell_confirm:
        entry = price_close
        sl = entry + SL_PIPS * pip_value(yf_symbol)
        tp = entry - TP_PIPS * pip_value(yf_symbol)
        lot = calculate_lot_for_risk(entry, sl, MAX_RISK_USD, yf_symbol)
        return {
            "type": "SELL",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rsi": rsi_last,
            "lot": lot
        }

    return None

# -------------------------
# Email
# -------------------------
def send_email(subject: str, html_body: str):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Email no enviado: faltan credenciales en variables de entorno.")
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
        return False

def build_html_message(label, sig):
    return f"""
    <html>
      <body>
        <h2>Señal confirmada — {label} ({sig['type']})</h2>
        <ul>
          <li><b>Entrada:</b> {sig['entry']:.5f}</li>
          <li><b>Stop Loss:</b> {sig['sl']:.5f}</li>
          <li><b>Take Profit:</b> {sig['tp']:.5f}</li>
          <li><b>RSI:</b> {sig['rsi']:.1f}</li>
          <li><b>Lote aprox:</b> {sig['lot']}</li>
          <li><b>Riesgo USD aprox:</b> {MAX_RISK_USD}</li>
        </ul>
        <p>Bot: EMA20/EMA50 + RSI + vela confirmatoria</p>
        <small>Generado: {dt.datetime.now(CR_TZ).strftime('%Y-%m-%d %H:%M:%S')}</small>
      </body>
    </html>
    """

# -------------------------
# MAIN: run once (cron)
# -------------------------
def main():
    print("=== BOT EJECUTANDO (yfinance) ===")
    any_signal = False
    for label, ticker in PAIRS.items():
        sig = analyze_pair(label, ticker)
        # pausa entre peticiones para reducir posibilidad de rate limit
        time.sleep(DELAY_BETWEEN_PAIRS)
        if sig:
            any_signal = True
            subj = f"Señal {sig['type']} {label} (EMA+RSI Confirmada)"
            html = build_html_message(label, sig)
            send_email(subj, html)
        else:
            print(f"— No hubo señal para {label}")
    if not any_signal:
        print("No hubo señales en esta ejecución.")
    print("=== Fin ejecución ===")

if __name__ == "__main__":
    main()
