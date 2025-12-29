# bot_alert_ema20_50_rsi_v2.py
import os
import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np
import pandas_ta as ta
from polygon import RESTClient  # Asegúrate de tener instalado polygon

# ================= CONFIGURACIÓN =================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

# Indicadores
EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

# Gestión de riesgo básica
SL_PIPS = 30
TP_PIPS = 30
MAX_RISK_USD = 1.50  # riesgo máximo por operación

# Control de frecuencia
COOLDOWN_HOURS = 1
STATE_FILE = "ema_state.json"

# Pares a monitorear (símbolos de Polygon para forex: prefijo C:)
PARES = [
    ("EURUSD", "C:EURUSD"),
    ("GBPUSD", "C:GBPUSD"),
    ("USDJPY", "C:USDJPY"),
]

# ================= UTILIDADES =================
def pip_size(symbol: str) -> float:
    """Tamaño de un pip según el par."""
    if "JPY" in symbol:
        return 0.01
    if "XAUUSD" in symbol:
        return 0.1
    return 0.0001

def to_1d(s):
    """Convierte a serie 1D float con índice limpio."""
    return pd.Series(s).astype(float).reset_index(drop=True)

def ema(series, span):
    """EMA exponencial con pandas."""
    return to_1d(series).ewm(span=span, adjust=False).mean()

def rsi(series, period):
    """RSI con pandas_ta, manejando NaN."""
    r = ta.rsi(to_1d(series), length=period)
    return r

# ================= ESTADO (COOLDOWN) =================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ================= LOTAJE =================
def lot_size(entry: float, sl: float, symbol: str) -> float:
    """Calcula lote en base a riesgo máximo, stop en pips y valor de pip."""
    pip_sz = pip_size(symbol)
    stop_pips = abs(entry - sl) / pip_sz

    # Aproximación de valor de pip para microlotes (ajústalo si tu broker difiere)
    pip_value = 10.0 if "JPY" not in symbol else 9.0

    if stop_pips <= 0:
        return 0.01

    lot = MAX_RISK_USD / (stop_pips * pip_value)
    lot = max(round(lot, 2), 0.01)  # mínimo microlote
    return lot

# ================= DATOS (Polygon) =================
def get_h1(symbol: str, days: int = 30) -> pd.DataFrame:
    """Descarga velas H1 de Polygon entre fechas, en UTC, y devuelve OHLC."""
    client = RESTClient(POLYGON_API_KEY)
    to_date = datetime.now(timezone.utc)
    from_date = to_date - timedelta(days=days)

    aggs = client.get_aggs(
        ticker=symbol,
        multiplier=1,
        timespan="hour",
        from_=from_date.date(),
        to=to_date.date(),
        limit=50000,
    )

    df = pd.DataFrame(aggs)
    if df.empty:
        return df

    # Polygon suele entregar timestamp en ms
    ts_col = "timestamp" if "timestamp" in df.columns else "t"
    df["timestamp"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)

    # Normaliza nombres de columnas posibles
    col_map = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
    }
    df = df.rename(columns=col_map)
    return df[["open", "high", "low", "close"]].dropna()

# ================= LÓGICA =================
def analyze(label: str, symbol: str, state: dict):
    print(f"\n→ Analizando {label}...")

    # Cooldown por par
    now = datetime.now(timezone.utc)
    last_ts_iso = state.get(label)
    if last_ts_iso:
        last_ts = datetime.fromisoformat(last_ts_iso)
        elapsed = (now - last_ts).total_seconds()
        if elapsed < COOLDOWN_HOURS * 3600:
            remaining = int((COOLDOWN_HOURS * 3600 - elapsed) // 60)
            print(f"  ⏸️ Cooldown activo ({remaining} min restantes)")
            return

    # Datos
    df = get_h1(symbol)
    if df is None or len(df) < max(EMA_SLOW + 5, RSI_PERIOD + 5):
        print("  ⚠️ Datos insuficientes para análisis")
        return

    close = df["close"]
    open_ = df["open"]

    # Indicadores
    ema20 = ema(close, EMA_FAST)
    ema50 = ema(close, EMA_SLOW)
    rsi_v = rsi(close, RSI_PERIOD)

    # Usar la vela cerrada previa
    c0, o0 = close.iloc[-2], open_.iloc[-2]

    # Logs detallados
    print(
        f"  EMA20: {ema20.iloc[-2]:.5f} | EMA50: {ema50.iloc[-2]:.5f} | "
        f"RSI: {rsi_v.iloc[-2]:.2f} | Cierre: {c0:.5f}"
    )

    # Condiciones relajadas (sin 'no ruptura')
    buy = (
        ema20.iloc[-3] <= ema50.iloc[-3]  # contexto: antes del cruce
        and ema20.iloc[-2] > ema50.iloc[-2]  # cruce confirmado en vela cerrada
        and rsi_v.iloc[-2] >= 52  # momentum a favor (ligeramente >50)
        and c0 > o0  # vela alcista
    )

    sell = (
        ema20.iloc[-3] >= ema50.iloc[-3]
        and ema20.iloc[-2] < ema50.iloc[-2]
        and rsi_v.iloc[-2] <= 48
        and c0 < o0  # vela bajista
    )

    if not (buy or sell):
        print("  ❌ No hay señal en esta vela cerrada")
        return

    direction = "BUY" if buy else "SELL"
    entry = c0

    # SL/TP en pips
    pip_sz = pip_size(symbol)
    sl = entry - SL_PIPS * pip_sz if buy else entry + SL_PIPS * pip_sz
    tp = entry + TP_PIPS * pip_sz if buy else entry - TP_PIPS * pip_sz

    lot = lot_size(entry, sl, symbol)

    # Imprimir señal para pruebas (email desactivado)
    print(f"\n✅ SEÑAL {direction} {label}")
    print(f"  Estrategia: EMA({EMA_FAST}/{EMA_SLOW}) + RSI({RSI_PERIOD})")
    print("  Timeframe: H1 (vela cerrada)")
    print(f"  Entrada (market): {entry:.5f}")
    print(f"  SL: {sl:.5f}  |  TP: {tp:.5f}")
    print(f"  Lote sugerido: {lot}\n")

    # Actualizar cooldown
    state[label] = now.isoformat()
    save_state(state)

# ================= MAIN =================
if __name__ == "__main__":
    print(f"=== BOT EMA 20/50 + RSI (UTC {datetime.now(timezone.utc).strftime('%H:%M')}) ===")
    state = load_state()

    for label, symbol in PARES:
        try:
            analyze(label, symbol, state)
            time.sleep(2)  # pequeño delay entre pares
        except Exception as e:
            print(f"  ⚠️ Error analizando {label}: {e}")

    print("\nCiclo terminado 🚀")
