import os
import yfinance as yf
import pandas as pd
import numpy as np
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# ============================
# CONFIG
# ============================
CR_TZ = pytz.timezone("America/Costa_Rica")

# Env (asegurar que coincida con bot.yml)
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")

# Strategy params
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

RSI_BUY = 55
RSI_SELL = 45

SL_PIPS_FOREX = 30
TP_PIPS_FOREX = 60

SL_USD_GOLD = 3
TP_USD_GOLD = 6

MAX_RISK_USD = 1.5
TIMEFRAME_MINUTES = 60

pairs = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "XAUUSD": "GC=F"
}

# ============================
# UTILS
# ============================
def to_1d(series):
    """Convierte DataFrame/Series/ndarray 1-columna a pd.Series 1D float."""
    if isinstance(series, pd.DataFrame):
        s = series.iloc[:, 0]
        return pd.Series(s).astype(float).reset_index(drop=True)
    arr = np.asarray(series)
    if arr.ndim == 2 and arr.shape[1] == 1:
        arr = arr.ravel()
        return pd.Series(arr).astype(float).reset_index(drop=True)
    return pd.Series(series).astype(float).reset_index(drop=True)

def pip_value(symbol):
    if symbol == "GC=F":       # ORO
        return 0.10
    if "JPY" in symbol:        # JPY pairs
        return 0.01
    return 0.0001              # Forex normal

def calculate_lot_for_risk(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)
    if sl_pips == 0:
        return 0.01

    # Value per pip for 0.01 lot (approx)
    if symbol == "GC=F":
        value_0_01 = 0.10  # conservative
    else:
        value_0_01 = 0.10

    lot = max_risk / (sl_pips * value_0_01)
    return max(round(lot, 2), 0.01)

# ============================
# INDICATORS
# ============================
def ema(series, span):
    s = to_1d(series)
    return s.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    s = to_1d(series)
    delta = s.diff()
    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)
    avg_gain = pd.Series(gain).rolling(window=period, min_periods=period).mean()
    avg_loss = pd.Series(loss).rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series.fillna(50)  # neutral for initial values

# ============================
# EMAIL
# ============================
def send_email(subject, body):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas; no se envía correo.")
        return False
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
        print(f"📧 Email enviado: {subject}")
        return True
    except Exception as e:
        print("❌ Error enviando correo:", e)
        return False

# ============================
# FETCH DATA (robusto)
# ============================
def fetch_ohlc_yf(symbol, period_minutes=60):
    try:
        df = yf.download(symbol, period="7d", interval=f"{period_minutes}m", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        # Normalize column names to lowercase
        df.columns = [str(c).lower() for c in df.columns]
        # Ensure open/close exist; attempt remap if multi columns appear
        if "open" not in df.columns or "close" not in df.columns:
            # try to find candidates
            cand_close = [c for c in df.columns if "close" in c]
            cand_open = [c for c in df.columns if "open" in c]
            if cand_close and cand_open:
                df = df.rename(columns={cand_close[0]: "close", cand_open[0]: "open"})
            else:
                # fallback: take first column as close
                first = df.columns[0]
                df = df.rename(columns={first: "close"})
                df["open"] = df["close"]
                df["high"] = df["close"]
                df["low"] = df["close"]
                df["volume"] = 0
        # Ensure numeric and 1D
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                if isinstance(df[col], pd.DataFrame):
                    df[col] = df[col].iloc[:, 0]
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        return df
    except Exception as e:
        print("Error al descargar datos:", e)
        return pd.DataFrame()

# ============================
# SIGNAL LOGIC (cruce previo + confirmacion 3 velas)
# ============================
def analyze_pair(label, yf_symbol):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos de {label} ({yf_symbol})...")
    df = fetch_ohlc_yf(yf_symbol, period_minutes=TIMEFRAME_MINUTES)
    if df.empty or len(df) < 60:
        print("— Sin datos suficientes.")
        return

    # normalize
    close = to_1d(df["close"])
    openv = to_1d(df["open"])

    ema_fast = ema(close, EMA_FAST)
    ema_slow = ema(close, EMA_SLOW)
    rsi_series = rsi(close, RSI_PERIOD)

    # extract numeric scalars
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

    # cruces previos
    cross_up_prev = (f2 <= s2) and (f1 > s1)
    cross_dn_prev = (f2 >= s2) and (f1 < s1)

    # confirmaciones (cualquiera de las 3 velas)
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

    # debug print of indicators (concise)
    print(f"  close_last={close_last:.5f} ema20={fl:.5f} ema50={sl:.5f} rsi={rsi_last:.1f}")

    # BUY
    if cross_up_prev and buy_confirm:
        print("📈 Señal BUY detectada ✓")
        if yf_symbol == "GC=F":
            entry = close_last
            slv = entry - SL_USD_GOLD
            tpv = entry + TP_USD_GOLD
        else:
            pip = pip_value(yf_symbol)
            entry = close_last
            slv = entry - SL_PIPS_FOREX * pip
            tpv = entry + TP_PIPS_FOREX * pip

        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, yf_symbol)
        msg = (
            f"📈 BUY Confirmado {label}\n\n"
            f"Entrada: {entry}\nSL: {slv}\nTP: {tpv}\nRSI: {rsi_last:.1f}\n"
            f"Riesgo: ${MAX_RISK_USD}\nLote sugerido: {lot}\n"
        )
        send_email(f"BUY Confirmado {label}", msg)
        print(f"  Enviado BUY {label} Entrada:{entry:.5f} SL:{slv:.5f} TP:{tpv:.5f} Lote:{lot}")
        return

    # SELL
    if cross_dn_prev and sell_confirm:
        print("📉 Señal SELL detectada ✓")
        if yf_symbol == "GC=F":
            entry = close_last
            slv = entry + SL_USD_GOLD
            tpv = entry - TP_USD_GOLD
        else:
            pip = pip_value(yf_symbol)
            entry = close_last
            slv = entry + SL_PIPS_FOREX * pip
            tpv = entry - TP_PIPS_FOREX * pip

        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, yf_symbol)
        msg = (
            f"📉 SELL Confirmado {label}\n\n"
            f"Entrada: {entry}\nSL: {slv}\nTP: {tpv}\nRSI: {rsi_last:.1f}\n"
            f"Riesgo: ${MAX_RISK_USD}\nLote sugerido: {lot}\n"
        )
        send_email(f"SELL Confirmado {label}", msg)
        print(f"  Enviado SELL {label} Entrada:{entry:.5f} SL:{slv:.5f} TP:{tpv:.5f} Lote:{lot}")
        return

    print(f"— No hubo señal para {label}")

# ============================
# MAIN
# ============================
if __name__ == "__main__":
    print("=== Bot ejecutándose (modo CRON) ===")
    for label, symbol in pairs.items():
        analyze_pair(label, symbol)
    print("Fin del ciclo.")
