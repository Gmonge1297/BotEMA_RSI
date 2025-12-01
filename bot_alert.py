import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import smtplib
from email.mime.text import MIMEText

# ==========================
# CONFIGURACIONES DEL BOT
# ==========================

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"
}

INTERVAL = "1h"
PERIOD = "7d"

EMA_FAST = 20
EMA_SLOW = 50
RSI_PERIOD = 14

# ==========================
# CONFIG EMAIL (editar)
# ==========================

EMAIL_USER = "TU_CORREO@gmail.com"
EMAIL_PASS = "TU_APP_PASSWORD"
EMAIL_TO   = "DESTINATARIO@gmail.com"

# ==========================
# FUNCIONES BOT
# ==========================

def log(msg):
    cr = datetime.now(pytz.timezone("America/Costa_Rica"))
    print(f"[ {cr} ] {msg}")

def send_email(subject, body):
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        print("📧 Email enviado exitosamente.")
    except Exception as e:
        print(f"❌ Error enviando email: {e}")

def calculate_indicators(df):
    """
    Calcula EMA20, EMA50 y RSI en el DataFrame y retorna df limpio.
    Asegura que las columnas resulten en series 1D alineadas con el index.
    """
    # Asegurar que 'Close' exista y sea float
    if "Close" not in df.columns:
        raise ValueError("No se encontró columna 'Close' en dataframe.")

    df = df.copy()
    df["Close"] = df["Close"].astype(float)

    # EMAs correctas
    df["EMA20"] = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # RSI (usando EMA smoothing)
    delta = df["Close"].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    roll_up = up.ewm(span=RSI_PERIOD, adjust=False).mean()
    roll_down = down.ewm(span=RSI_PERIOD, adjust=False).mean()

    rs = roll_up / roll_down
    df["RSI"] = 100 - (100 / (1 + rs))

    # Eliminar filas con NaN resultantes de los cálculos
    df = df.dropna().copy()

    return df

# ==============================================================
# Funciones que usan valores escalares (.iat) para evitar Series
# ==============================================================

def check_trend_reversal(df):
    """
    Devuelve 'down' si detecta cruce bajista EMA20<EMA50 en la última vela,
    'up' si detecta cruce alcista EMA20>EMA50 en la última vela, o None.
    Usa valores escalares para evitar ambigüedades.
    """
    # Asegurar al menos 2 barras
    if len(df) < 2:
        return None

    ema20_prev = float(df["EMA20"].iat[-2])
    ema50_prev = float(df["EMA50"].iat[-2])
    ema20_last = float(df["EMA20"].iat[-1])
    ema50_last = float(df["EMA50"].iat[-1])

    # Cruce bajista
    if ema20_prev > ema50_prev and ema20_last < ema50_last:
        return "down"
    # Cruce alcista
    if ema20_prev < ema50_prev and ema20_last > ema50_last:
        return "up"
    return None

def check_signal(df):
    """
    Retorna dict con señal como en el bot original o None.
    Usa .iat para extraer valores escalares (evita ValueError con Series).
    """
    if len(df) < 1:
        return None

    # Extraer escalares
    ema20 = float(df["EMA20"].iat[-1])
    ema50 = float(df["EMA50"].iat[-1])
    rsi   = float(df["RSI"].iat[-1])
    price = float(df["Close"].iat[-1])
    openp = float(df["Open"].iat[-1])

    bull = price > openp
    bear = price < openp

    # BUY
    if ema20 > ema50 and rsi > 50 and bull:
        return {
            "type": "BUY",
            "price": price,
            "sl": price - 18,
            "tp": price + 36,
            "rsi": rsi
        }

    # SELL
    if ema20 < ema50 and rsi < 50 and bear:
        return {
            "type": "SELL",
            "price": price,
            "sl": price + 18,
            "tp": price - 36,
            "rsi": rsi
        }

    return None

def build_email(name, signal):
    now = datetime.now(pytz.timezone("America/Costa_Rica")).strftime("%Y-%m-%d %H:%M:%S")
    return f"""
Señal confirmada — {name} ({signal['type']})

Entrada: {signal['price']:.5f}
Stop Loss: {signal['sl']:.5f}
Take Profit: {signal['tp']:.5f}
RSI: {signal['rsi']:.1f}
Lote sugerido: 0.01
Riesgo por trade (USD aprox): 1.00

Bot: EMA20/EMA50 + RSI (flex) + vela confirmatoria
Generado: {now}
"""

def build_partial_close_email(name, entry, current):
    now = datetime.now(pytz.timezone("America/Costa_Rica")).strftime("%Y-%m-%d %H:%M:%S")
    profit = current - entry
    return f"""
⚠️ Cerrar ahora — Ganancia parcial recomendada  
{name}

La operación aún iba en positivo, pero se detectó un cambio de tendencia.

Entrada: {entry:.5f}
Precio actual: {current:.5f}
Ganancia actual: {profit:.2f}

Razón: Cruce inverso EMA20/EMA50 detectado.

Generado: {now}
"""

# ==============================================================

active_trades = {}  # en memoria; opcional: persistir en archivo si querés

def _flatten_columns(df):
    """
    Si df.columns es MultiIndex, devuelve una lista con la última etiqueta
    de cada tupla; si no, devuelve la list(df.columns).
    También renombra 'Adj Close' -> 'Close' por seguridad.
    """
    cols = []
    if isinstance(df.columns, pd.MultiIndex):
        for c in df.columns:
            # tomar la última etiqueta no vacía del tuple
            nc = None
            for part in reversed(c):
                if part and str(part).strip() != "":
                    nc = str(part)
                    break
            if nc is None:
                nc = str(c[-1])
            if nc == "Adj Close":
                nc = "Close"
            cols.append(nc)
    else:
        for c in df.columns:
            nc = str(c)
            if nc == "Adj Close":
                nc = "Close"
            cols.append(nc)
    return cols

def process_symbol(name, symbol):
    global active_trades

    log(f"Descargando datos de {name} ({symbol})...")

    try:
        # activar auto_adjust=True evita futuros warnings y deja precios "limpios"
        df = yf.download(symbol, interval=INTERVAL, period=PERIOD, progress=False, auto_adjust=True)
    except Exception as e:
        print(f"❌ Error descargando {name}: {e}\n")
        return

    if df is None or df.empty or len(df) < 2:
        print("⚠️ Sin suficientes datos.\n")
        return

    # ==================================================
    # Normalizar/flatten columnas (resuelve el error)
    # ==================================================
    try:
        new_cols = _flatten_columns(df)
        df.columns = new_cols
    except Exception as e:
        print(f"⚠️ Error al normalizar columnas para {name}: {e}")
        print("Columnas originales:", df.columns)
        return

    if "Close" not in df.columns or "Open" not in df.columns:
        print("⚠️ Data no contiene 'Open' o 'Close'. Columnas:", df.columns.tolist())
        return

    # eliminar nulos mínimos y validar longitud
    df = df.dropna().copy()
    if len(df) < 60:
        print("⚠️ No hay suficientes velas (mínimo 60) después de dropna.\n")
        return

    # calcular indicadores
    try:
        df = calculate_indicators(df)
    except Exception as e:
        print(f"❌ Error calculando indicadores para {name}: {e}\n")
        return

    # DEBUG: valores escalares seguros
    try:
        close_last = float(df["Close"].iat[-1])
        ema20_last = float(df["EMA20"].iat[-1])
        ema50_last = float(df["EMA50"].iat[-1])
        rsi_last = float(df["RSI"].iat[-1])
        print(f"DEBUG {name} → Close:{close_last:.5f}, EMA20:{ema20_last:.5f}, EMA50:{ema50_last:.5f}, RSI:{rsi_last:.2f}")
    except Exception as e:
        print(f"⚠️ Error al leer últimos valores para debug en {name}: {e}")
        print(df.tail(2))

    # 1) nueva señal
    try:
        signal = check_signal(df)
    except Exception as e:
        print(f"❌ Error en check_signal para {name}: {e}\n")
        signal = None

    if signal:
        print(f"⚡ Señal encontrada en {name}\n")
        active_trades[name] = signal
        email = build_email(name, signal)
        send_email(f"Señal — {name}", email)
        return

    # 2) revisar trades activos para posible cierre parcial
    if name in active_trades:
        entry = active_trades[name]["price"]
        tp = active_trades[name]["tp"]
        direction = active_trades[name]["type"]
        price = float(df["Close"].iat[-1])

        tp_dist = abs(tp - entry)
        if tp_dist == 0:
            print("⚠️ TP igual a entry, ignorando progreso.\n")
            return

        gain_now = abs(price - entry)
        progress = gain_now / tp_dist

        reversal = None
        try:
            reversal = check_trend_reversal(df)
        except Exception as e:
            print(f"⚠️ Error detectando reversión en {name}: {e}")

        # Si progreso >= 70% y hay reversión contraria, sugerir cerrar parcial
        if progress >= 0.70:
            if (direction == "BUY" and reversal == "down") or (direction == "SELL" and reversal == "up"):
                print(f"⚠️ Cambio de tendencia detectado en {name}. Enviando alerta de cierre parcial...")
                email = build_partial_close_email(name, entry, price)
                send_email(f"⚠️ Cerrar con ganancia parcial — {name}", email)
                del active_trades[name]
                return

    print("ℹ️ Sin señales nuevas.\n")

# ======================
# MAIN
# ======================

if __name__ == "__main__":
    print("\n=== Bot EMA+RSI (Actualizado con alerta de cierre parcial) ===\n")
    for name, symbol in SYMBOLS.items():
        process_symbol(name, symbol)
    print("\n=== Fin ejecución ===\n")
