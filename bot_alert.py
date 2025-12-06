import os
import pandas as pd
import numpy as np
import pytz
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from polygon import RESTClient  # Usa Polygon para datos precisos

# ============================
# CONFIG
# ============================
CR_TZ = pytz.timezone("America/Costa_Rica")

# Env vars
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")  # Agrega tu key de Polygon.io

# Strategy params (tu estrategia propuesta)
EMA_FAST = 8
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_BUY = 55
RSI_SELL = 45

ATR_PERIOD = 14  # Para SL/TP dinámico
SL_ATR_MULT = 1.5  # SL = ATR * 1.5
TP_ATR_MULT = 2.5  # TP = ATR * 2.5

MIN_SL_PIPS_FOREX = 20
MIN_TP_PIPS_FOREX = 40
MIN_SL_USD_GOLD = 2
MIN_TP_USD_GOLD = 4

MAX_RISK_USD = 1.5
TIMEFRAME_MINUTES = 60  # H1
TIMEFRAME_HIGHER = 240  # H4 para filtro

pairs = {
    "EURUSD": "C:EURUSD",
    "GBPUSD": "C:GBPUSD",
    "USDJPY": "C:USDJPY",
    "XAUUSD": "C:XAUUSD"
}

# Noticias alto impacto (expande con más fechas; o integra API)
HIGH_IMPACT_DATES = ["2025-12-05"]  # Ej: NFP día

# ============================
# UTILS
# ============================
def to_1d(series):
    if isinstance(series, pd.DataFrame):
        s = series.iloc[:, 0]
        return pd.Series(s).astype(float).reset_index(drop=True)
    arr = np.asarray(series)
    if arr.ndim == 2 and arr.shape[1] == 1:
        arr = arr.ravel()
        return pd.Series(arr).astype(float).reset_index(drop=True)
    return pd.Series(series).astype(float).reset_index(drop=True)

def pip_value(symbol):
    if "XAUUSD" in symbol:
        return 0.10
    if "JPY" in symbol:
        return 0.01
    return 0.0001

def calculate_lot_for_risk(entry, sl, max_risk, symbol):
    pip = pip_value(symbol)
    sl_pips = abs((entry - sl) / pip)
    if sl_pips == 0:
        return 0.01
    value_0_01 = 0.10 if "XAUUSD" not in symbol else 0.10
    lot = max_risk / (sl_pips * value_0_01)
    return max(round(lot, 2), 0.01)

def is_high_impact_day():
    today = datetime.now().strftime("%Y-%m-%d")
    return today in HIGH_IMPACT_DATES

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
    return rsi_series.fillna(50)

def atr(high, low, close, period=14):
    h_l = to_1d(high) - to_1d(low)
    h_c = abs(to_1d(high) - to_1d(close).shift(1))
    l_c = abs(to_1d(low) - to_1d(close).shift(1))
    tr = pd.DataFrame({'h_l': h_l, 'h_c': h_c, 'l_c': l_c}).max(axis=1)
    return tr.rolling(window=period).mean()

# ============================
# EMAIL
# ============================
def send_email(subject, body):
    if not EMAIL_USER or not EMAIL_PASSWORD or not EMAIL_TO:
        print("⚠️ Credenciales email no configuradas.")
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
# FETCH DATA (con Polygon)
# ============================
def fetch_ohlc_polygon(symbol, timeframe_minutes=60, days_back=7):
    if not POLYGON_API_KEY:
        print("⚠️ Polygon API key no configurada.")
        return pd.DataFrame()
    try:
        client = RESTClient(POLYGON_API_KEY)
        end = datetime.now()
        start = end - timedelta(days=days_back)
        aggs = client.get_aggs(symbol, 1, f"{timeframe_minutes} minute", start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        if not aggs:
            return pd.DataFrame()
        df = pd.DataFrame(aggs)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'})
        df.set_index('timestamp', inplace=True)
        df = df.astype(float)
        return df
    except Exception as e:
        print("Error al descargar datos:", e)
        return pd.DataFrame()

# ============================
# SIGNAL LOGIC (mejorada)
# ============================
def analyze_pair(label, poly_symbol):
    if is_high_impact_day():
        print(f"— Skipping {label} por día de alto impacto.")
        return

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Descargando datos H1 de {label}...")
    df_h1 = fetch_ohlc_polygon(poly_symbol, TIMEFRAME_MINUTES)
    if df_h1.empty or len(df_h1) < 60:
        print("— Sin datos suficientes H1.")
        return

    print("Descargando datos H4 para filtro...")
    df_h4 = fetch_ohlc_polygon(poly_symbol, TIMEFRAME_HIGHER, days_back=14)  # Más días para H4
    if df_h4.empty or len(df_h4) < 20:
        print("— Sin datos suficientes H4.")
        return

    # Indicadores H1
    close_h1 = to_1d(df_h1["close"])
    open_h1 = to_1d(df_h1["open"])
    high_h1 = to_1d(df_h1["high"])
    low_h1 = to_1d(df_h1["low"])

    ema_fast_h1 = ema(close_h1, EMA_FAST)
    ema_slow_h1 = ema(close_h1, EMA_SLOW)
    rsi_h1 = rsi(close_h1, RSI_PERIOD)
    atr_h1 = atr(high_h1, low_h1, close_h1, ATR_PERIOD)

    # Indicadores H4 (filtro tendencia)
    close_h4 = to_1d(df_h4["close"])
    ema50_h4 = ema(close_h4, 50)
    ema200_h4 = ema(close_h4, 200)

    # Valores últimos H1
    f2_h1 = float(ema_fast_h1.iat[-3])
    s2_h1 = float(ema_slow_h1.iat[-3])
    f1_h1 = float(ema_fast_h1.iat[-2])
    s1_h1 = float(ema_slow_h1.iat[-2])
    fl_h1 = float(ema_fast_h1.iat[-1])
    sl_h1 = float(ema_slow_h1.iat[-1])

    close_last = float(close_h1.iat[-1])
    open_last = float(open_h1.iat[-1])
    close_prev = float(close_h1.iat[-2])
    open_prev = float(open_h1.iat[-2])

    rsi_last = float(rsi_h1.iat[-1])
    atr_last = float(atr_h1.iat[-1])

    # Filtro H4
    trend_up_h4 = float(ema50_h4.iat[-1]) > float(ema200_h4.iat[-1])
    trend_dn_h4 = float(ema50_h4.iat[-1]) < float(ema200_h4.iat[-1])

    # Cruces previos H1
    cross_up_prev = (f2_h1 <= s2_h1) and (f1_h1 > s1_h1)
    cross_dn_prev = (f2_h1 >= s2_h1) and (f1_h1 < s1_h1)

    # Confirmación estricta (últimas 2 velas)
    buy_confirm = (
        ((close_last > open_last) and (close_last > fl_h1) and (close_last > sl_h1)) and
        ((close_prev > open_prev) and (close_prev > fl_h1) and (close_prev > sl_h1))
    ) and (rsi_last > RSI_BUY) and trend_up_h4  # Solo si tendencia H4 up

    sell_confirm = (
        ((close_last < open_last) and (close_last < fl_h1) and (close_last < sl_h1)) and
        ((close_prev < open_prev) and (close_prev < fl_h1) and (close_prev < sl_h1))
    ) and (rsi_last < RSI_SELL) and trend_dn_h4  # Solo si tendencia H4 down

    print(f"  close_last={close_last:.5f} ema8={fl_h1:.5f} ema21={sl_h1:.5f} rsi={rsi_last:.1f} atr={atr_last:.5f} trend_h4_up={trend_up_h4}")

    # BUY
    if cross_up_prev and buy_confirm:
        print("📈 Señal BUY detectada ✓")
        entry = close_last
        pip = pip_value(poly_symbol)
        sl_dist = max(atr_last * SL_ATR_MULT, MIN_SL_PIPS_FOREX * pip if "XAUUSD" not in poly_symbol else MIN_SL_USD_GOLD)
        tp_dist = max(atr_last * TP_ATR_MULT, MIN_TP_PIPS_FOREX * pip if "XAUUSD" not in poly_symbol else MIN_TP_USD_GOLD)
        slv = entry - sl_dist
        tpv = entry + tp_dist
        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, poly_symbol)
        msg = f"📈 BUY {label}\nEntrada: {entry}\nSL: {slv}\nTP: {tpv}\nRSI: {rsi_last:.1f}\nRiesgo: ${MAX_RISK_USD}\nLote: {lot}"
        send_email(f"BUY {label}", msg)
        return

    # SELL
    if cross_dn_prev and sell_confirm:
        print("📉 Señal SELL detectada ✓")
        entry = close_last
        pip = pip_value(poly_symbol)
        sl_dist = max(atr_last * SL_ATR_MULT, MIN_SL_PIPS_FOREX * pip if "XAUUSD" not in poly_symbol else MIN_SL_USD_GOLD)
        tp_dist = max(atr_last * TP_ATR_MULT, MIN_TP_PIPS_FOREX * pip if "XAUUSD" not in poly_symbol else MIN_TP_USD_GOLD)
        slv = entry + sl_dist
        tpv = entry - tp_dist
        lot = calculate_lot_for_risk(entry, slv, MAX_RISK_USD, poly_symbol)
        msg = f"📉 SELL {label}\nEntrada: {entry}\nSL: {slv}\nTP: {tpv}\nRSI: {rsi_last:.1f}\nRiesgo: ${MAX_RISK_USD}\nLote: {lot}"
        send_email(f"SELL {label}", msg)
        return

    print(f"— No hubo señal para {label}")

# ============================
# MAIN (con backtest simple opcional)
# ============================
if __name__ == "__main__":
    print("=== Bot ejecutándose (modo CRON) ===")
    for label, symbol in pairs.items():
        analyze_pair(label, symbol)
    print("Fin del ciclo.")
    
    # Opcional: Backtest rápido para un par (ej. GBPUSD últimos 30 días)
    # df_test = fetch_ohlc_polygon("C:GBPUSD", 60, days_back=30)
    # if not df_test.empty:
    #     # Aplica lógica a todo df y cuenta señales/gains hipotéticos
    #     print("Backtest: Simula aquí...")
