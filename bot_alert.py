import yfinance as yf
import pandas as pd
import json
from datetime import datetime

# ======================================================
# CONFIG
# ======================================================

INTERVAL = "15m"
PERIOD = "5d"

SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GOLD": "GC=F"
}

LAST_TRADE_FILE = "last_trade.json"


# ======================================================
# FUNCIONES AUXILIARES
# ======================================================

def cargar_ultimo_trade():
    try:
        with open(LAST_TRADE_FILE, "r") as f:
            return json.load(f)
    except:
        return None


def guardar_ultimo_trade(data):
    with open(LAST_TRADE_FILE, "w") as f:
        json.dump(data, f, indent=4)


# ======================================================
# INDICADORES
# ======================================================

def calcular_RSI(df, period=14):
    delta = df["Close"].diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)

    roll_up = pd.Series(up).rolling(period).mean()
    roll_down = pd.Series(down).rolling(period).mean()

    RS = roll_up / roll_down
    rsi = 100 - (100 / (1 + RS))
    df["RSI"] = rsi
    return df


def calcular_indicadores(df):
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df = calcular_RSI(df)
    return df.dropna().reset_index(drop=True)


# ======================================================
# DETECTAR SEÑAL DE ENTRADA (AGRESIVA)
# ======================================================

def detectar_senal(df):
    # Asegurar índice limpio
    df = df.dropna().reset_index(drop=True)
    if len(df) < 3:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # BUY agresivo
    if (
        prev["EMA20"] < prev["EMA50"] and
        last["EMA20"] > last["EMA50"] and
        last["RSI"] > 50
    ):
        return "BUY"

    # SELL agresivo
    if (
        prev["EMA20"] > prev["EMA50"] and
        last["EMA20"] < last["EMA50"] and
        last["RSI"] < 50
    ):
        return "SELL"

    return None


# ======================================================
# DETECTAR SEÑAL DE SALIDA
# ======================================================

def check_exit_signal(df, entry_price, direction):
    df = df.dropna().reset_index(drop=True)
    if len(df) < 5:
        return False

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    ema20 = df["EMA20"]

    conditions = 0

    # 1) EMA20 plana por 3 velas
    if (
        abs(ema20.iloc[-1] - ema20.iloc[-2]) < 0.10 and
        abs(ema20.iloc[-2] - ema20.iloc[-3]) < 0.10
    ):
        conditions += 1

    # 2) RSI contratrend
    if direction == "BUY" and latest["RSI"] < 48:
        conditions += 1
    if direction == "SELL" and latest["RSI"] > 52:
        conditions += 1

    # 3) Dos velas contra tendencia respecto a EMA20
    if direction == "BUY":
        if latest["Close"] < latest["EMA20"] and prev["Close"] < prev["EMA20"]:
            conditions += 1
    else:
        if latest["Close"] > latest["EMA20"] and prev["Close"] > prev["EMA20"]:
            conditions += 1

    # 4) Vela fuerte rompe extremos recientes
    if direction == "BUY":
        if latest["Low"] < min(df["Low"].iloc[-4:-1]):
            conditions += 1
    else:
        if latest["High"] > max(df["High"].iloc[-4:-1]):
            conditions += 1

    if conditions < 2:
        return False

    # ¿Está en ganancias?
    current_price = latest["Close"]

    if direction == "BUY" and current_price > entry_price:
        return True
    if direction == "SELL" and current_price < entry_price:
        return True

    return False


# ======================================================
# PROCESAR UN SÍMBOLO
# ======================================================

def procesar_simbolo(nombre, yf_symbol):
    print(f"\n[ {datetime.now()} ] Descargando datos de {nombre} ({yf_symbol})...")

    df = yf.download(yf_symbol, interval=INTERVAL, period=PERIOD, progress=False)
    if df is None or len(df) < 10:
        print("No hay suficientes datos.")
        return

    df = calcular_indicadores(df)

    ultimo = cargar_ultimo_trade()

    # Si no hay trade abierto → buscar señal de entrada
    if not ultimo:
        senal = detectar_senal(df)
        if senal:
            entry = df.iloc[-1]["Close"]
            trade = {
                "symbol": nombre,
                "direction": senal,
                "entry_price": float(entry),
                "timestamp": str(datetime.now())
            }
            guardar_ultimo_trade(trade)
            print(f"⚡ NUEVA SEÑAL {senal} en {nombre} — Entry {entry}")
        return

    # Si hay trade abierto → buscar salida
    if ultimo["symbol"] == nombre:
        salir = check_exit_signal(
            df,
            ultimo["entry_price"],
            ultimo["direction"]
        )

        if salir:
            guardar_ultimo_trade(None)
            print(f"🔔 ALERTA DE SALIDA: {nombre} — cerrar trade en profit")
        else:
            print(f"No hay salida aún ({nombre}).")


# ======================================================
# MAIN
# ======================================================

print("=== Bot EMA+RSI Agresivo ===")

for nombre, yf_symbol in SYMBOLS.items():
    procesar_simbolo(nombre, yf_symbol)
