import pandas as pd
import numpy as np
import yfinance as yf
import datetime as dt
import pytz
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------- VARIABLES DE ENTORNO ----------
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# ---------- PARÁMETROS DE ESTRATEGIA ----------
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
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

# =========================
# UTIL: normalizar a 1D
# =========================
def to_1d(series):
    """
    Convierte Series, DataFrame de 1 columna o arrays Nx1 a pandas Series 1D float.
    """
    if isinstance(series, pd.DataFrame):
        # tomar la primera columna
        s = series.iloc[:, 0]
        return pd.Series(s).astype(float).reset_index(drop=True)
    # si es numpy ndarray con shape (N,1)
    arr = np.asarray(series)
    if arr.ndim == 2 and arr.shape[1] == 1:
        arr = arr.ravel()
        return pd.Series(arr).astype(float).reset_index(drop=True)
    # ya es Serie 1D o similar
    return pd.Series(series).astype(float).reset_index(drop=True)

# ---------- INDICADORES ----------
def ema(series, span):
    # espera una pandas Series 1D
    s = to_1d(series)
    return s.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    s = to_1d(series)
    delta = s.diff()
    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)

    # usar rolling mean para compatibilidad
    avg_gain = pd.Series(gain).rolling(window=period, min_periods=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period, min_periods=period).mean()

    # evitar división por cero
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series.fillna(50)  # rellenar iniciales con 50 (neutro)

# ---------- ENVÍO DE CORREO ----------
def send_email(subject, body):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
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
        print("Correo enviado:", subject)
    except Exception as e:
        print("Error enviando correo:", e)

# ---------- PIP VALUE / RIESGO ----------
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

# ---------- DESCARGA DE DATOS ----------
def fetch_ohlc_yf(symbol, period_minutes=60):
    try:
        df = yf.download(symbol, period="7d", interval=f"{period_minutes}m", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        # Normalizar nombres a minúsculas
        df.columns = [str(c).lower() for c in df.columns]

        # Si no vienen open/close (caso columnas con nombre del ticker), intentar mapear
        if "open" not in df.columns or "close" not in df.columns:
            # si tiene una sola columna -> usarla como close y crear open=close
            if df.shape[1] == 1:
                col0 = df.columns[0]
                df = df.rename(columns={col0: "close"})
                df["open"] = df["close"]
                df["high"] = df["close"]
                df["low"] = df["close"]
                df["volume"] = 0
            else:
                # intentar detectar columnas tipo ('EURUSD=X','Open') en multiindex -- ya bajoneado a str
                # si existe 'close' en alguna forma, buscarla
                cand_close = [c for c in df.columns if "close" in c]
                cand_open = [c for c in df.columns if "open" in c]
                if cand_close and cand_open:
                    df = df.rename(columns={cand_close[0]:"close", cand_open[0]:"open"})
                else:
                    # último recurso: tomar la primera como close
                    first = df.columns[0]
                    df = df.rename(columns={first: "close"})
                    df["open"] = df["close"]
                    df["high"] = df["close"]
                    df["low"] = df["close"]
                    df["volume"] = 0

        # Asegurar tipos float y 1D para columnas críticas
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                # si es DataFrame 2D en una columna, coger la primera subcol
                if isinstance(df[col], pd.DataFrame):
                    df[col] = df[col].iloc[:, 0]
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["close"])  # quitar filas sin close
        return df

    except Exception as e:
        print("Error al descargar datos:", e)
        return pd.DataFrame()

# ---------- ESTRATEGIA CON VELA DE CONFIRMACIÓN ----------
def analyze_pair(label, yf_symbol):
    print(f"Descargando datos de {label}...")

    df = fetch_ohlc_yf(yf_symbol, period_minutes=TIMEFRAME_MINUTES)
    if df.empty or len(df) < 60:
        print("Sin datos suficientes.")
        return

    # CORRECCIÓN: asegurar vectores 1D
    close = to_1d(df["close"])
    openv = to_1d(df["open"])

    # calcular indicadores (1D)
    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    # velas prev_prev = -3, prev = -2, last = -1
    # extraer EMAs escalares
    f2 = float(ema_fast.iat[-3])
    s2 = float(ema_slow.iat[-3])
    f1 = float(ema_fast.iat[-2])
    s1 = float(ema_slow.iat[-2])
    fl = float(ema_fast.iat[-1])
    sl = float(ema_slow.iat[-1])

    close_last = float(close.iat[-1])
    open_last = float(openv.iat[-1])
    close_prev = float(close.iat[-2])
    open_prev = float(openv.iat[-2])
    close_prev2 = float(close.iat[-3])
    open_prev2 = float(openv.iat[-3])

    rsi_last = float(rsi_series.iat[-1])

    # cruces previos (misma lógica que tenías originalmente)
    cross_up_prev = (f2 <= s2) and (f1 > s1)
    cross_dn_prev = (f2 >= s2) and (f1 < s1)

    # Confirmación con 3 velas (cualquiera de las 3 puede confirmar)
    buy_confirm_any = (
        ((close_last > open_last) and (close_last > fl) and (close_last > sl)) or
        ((close_prev > open_prev) and (close_prev > fl) and (close_prev > sl)) or
        ((close_prev2 > open_prev2) and (close_prev2 > fl) and (close_prev2 > sl))
    )

    sell_confirm_any = (
        ((close_last < open_last) and (close_last < fl) and (close_last < sl)) or
        ((close_prev < open_prev) and (close_prev < fl) and (close_prev < sl)) or
        ((close_prev2 < open_prev2) and (close_prev2 < fl) and (close_prev2 < sl))
    )

    buy_confirm = buy_confirm_any and (rsi_last > RSI_BUY)
    sell_confirm = sell_confirm_any and (rsi_last < RSI_SELL)

    # BUY
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
        return

    # SELL
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
        return

# ---------- LOOP PRINCIPAL ----------
if __name__ == "__main__":
    print("=== Bot ejecutándose (modo CRON) ===")

    for label, symbol in pairs.items():
        analyze_pair(label, symbol)

    print("No hubo señales esta hora.")
