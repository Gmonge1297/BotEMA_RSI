import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime


# ============================================================
#   CONFIGURACIÓN
# ============================================================

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"     # Oro
}

INTERVAL = "1h"
PERIOD = "7d"

FILE_LAST_TRADE = "last_trade.json"

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")


# ============================================================
#   FUNCIONES DE EMAIL
# ============================================================

def enviar_correo(asunto, mensaje):
    if not EMAIL_USER or not EMAIL_PASS or not EMAIL_TO:
        print("⚠️ No hay credenciales de correo configuradas.")
        return

    msg = MIMEText(mensaje, "plain")
    msg["Subject"] = asunto
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        print("📧 Correo enviado.")
    except Exception as e:
        print(f"Error enviando correo: {e}")


# ============================================================
#   GUARDAR Y LEER ÚLTIMO TRADE
# ============================================================

def cargar_last_trade():
    if not os.path.exists(FILE_LAST_TRADE):
        return None
    try:
        with open(FILE_LAST_TRADE, "r") as f:
            data = json.load(f)
        return data
    except:
        return None


def guardar_last_trade(data):
    with open(FILE_LAST_TRADE, "w") as f:
        json.dump(data, f, indent=4)


def limpiar_last_trade():
    with open(FILE_LAST_TRADE, "w") as f:
        f.write("{}")


# ============================================================
#   ESTRATEGIA PRINCIPAL: EMA20 / EMA50 + RSI + VELA CONFIRMATORIA
# ============================================================

def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()

    delta = df["Close"].diff()
    up = np.where(delta > 0, delta, 0)
    down = np.where(delta < 0, -delta, 0)
    roll_up = pd.Series(up).rolling(14).mean()
    roll_down = pd.Series(down).rolling(14).mean()
    rs = roll_up / roll_down
    df["RSI"] = 100 - (100 / (1 + rs))

    return df


def detectar_entrada(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    ema20 = df["EMA20"]
    ema50 = df["EMA50"]

    # BUY
    if (
        prev["EMA20"] < prev["EMA50"] and
        last["EMA20"] > last["EMA50"] and
        last["RSI"] > 55 and last["RSI"] < 70 and
        last["Close"] > last["Open"]
    ):
        return "BUY"

    # SELL
    if (
        prev["EMA20"] > prev["EMA50"] and
        last["EMA20"] < last["EMA50"] and
        last["RSI"] < 45 and last["RSI"] > 30 and
        last["Close"] < last["Open"]
    ):
        return "SELL"

    return None


# ============================================================
#   ESTRATEGIA DE SALIDA ANTICIPADA (EXIT SIGNAL)
# ============================================================

def check_exit_signal(df, entry_price, direction):
    """
    Detecta si hay señal de reversa.
    Requiere que al menos 2 condiciones se cumplan.
    """

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    conditions = 0

    # 1. EMA20 se aplana
    ema20 = df["EMA20"]
    if abs(ema20.iloc[-1] - ema20.iloc[-2]) < 0.10 and abs(ema20.iloc[-2] - ema20.iloc[-3]) < 0.10:
        conditions += 1

    # 2. RSI cambia de dirección
    if direction == "BUY" and latest["RSI"] < 48:
        conditions += 1
    if direction == "SELL" and latest["RSI"] > 52:
        conditions += 1

    # 3. Dos velas contra tendencia
    if direction == "BUY":
        if latest["Close"] < ema20.iloc[-1] and prev["Close"] < ema20.iloc[-2]:
            conditions += 1
    else:
        if latest["Close"] > ema20.iloc[-1] and prev["Close"] > ema20.iloc[-2]:
            conditions += 1

    # 4. Vela fuerte rompe niveles
    if direction == "BUY":
        if latest["Low"] < min(df["Low"].iloc[-4:-1]):
            conditions += 1
    else:
        if latest["High"] > max(df["High"].iloc[-4:-1]):
            conditions += 1

    # Necesitamos mínimo 2 condiciones
    if conditions < 2:
        return False

    # Confirmar que estamos en ganancias
    current_price = latest["Close"]

    if direction == "BUY" and current_price > entry_price:
        return True
    if direction == "SELL" and current_price < entry_price:
        return True

    return False


# ============================================================
#   TOMA DE BENEFICIO Y STOP LOSS
# ============================================================

def calcular_sl_tp(direction, entry_price, atr=30):
    if direction == "BUY":
        sl = entry_price - atr
        tp = entry_price + atr * 2
    else:
        sl = entry_price + atr
        tp = entry_price - atr * 2
    return sl, tp


# ============================================================
#   PROCESO PRINCIPAL
# ============================================================

def procesar_simbolo(nombre, yf_symbol):
    print(f"[{datetime.now()}] Descargando datos de {nombre} ({yf_symbol})...")

    df = yf.download(yf_symbol, interval=INTERVAL, period=PERIOD, progress=False)

    if df is None or len(df) < 60:
        print(f"❌ No hay datos suficientes para {nombre}")
        return

    df = calcular_indicadores(df)
    df = df.dropna()

    # --------------------------------------------------------
    # 1. Revisar si hay un trade abierto
    # --------------------------------------------------------
    last_trade = cargar_last_trade()

    if last_trade and last_trade.get("symbol") == nombre:
        direction = last_trade["direction"]
        entry = last_trade["entry"]

        if check_exit_signal(df, entry, direction):
            msg = f"""⛔ SALIDA ANTICIPADA (Reversal Detectado)
Símbolo: {nombre}
Dirección: {direction}
Entrada: {entry}
Precio actual: {df['Close'].iloc[-1]}

El bot recomienda CERRAR YA para asegurar ganancias.
"""
            enviar_correo(f"SALIDA ANTICIPADA - {nombre}", msg)
            print(msg)
            limpiar_last_trade()
            return

    # --------------------------------------------------------
    # 2. Buscar nueva entrada
    # --------------------------------------------------------
    señal = detectar_entrada(df)

    if señal:
        entry_price = df["Close"].iloc[-1]
        sl, tp = calcular_sl_tp(señal, entry_price)

        msg = f"""Señal confirmada — {nombre} ({señal})
Entrada: {entry_price:.5f}
Stop Loss: {sl:.5f}
Take Profit: {tp:.5f}
RSI: {df['RSI'].iloc[-1]:.1f}
Bot: EMA20/EMA50 + RSI + Confirmación

Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        enviar_correo(f"📈 Señal {nombre} - {señal}", msg)
        print(msg)

        # Guardar trade
        guardar_last_trade({
            "symbol": nombre,
            "direction": señal,
            "entry": entry_price
        })

    else:
        print(f"— No hubo señal para {nombre}\n")


# ============================================================
#   MAIN
# ============================================================

if __name__ == "__main__":
    print("=== Bot Intermedio: EMA20/EMA50 + RSI + Vela confirmatoria ===\n")

    for nombre, yf_symbol in SYMBOLS.items():
        procesar_simbolo(nombre, yf_symbol)

    print("\n=== Fin ejecución ===")
