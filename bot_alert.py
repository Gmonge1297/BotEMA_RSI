import yfinance as yf
import pandas as pd
import numpy as np
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==============================================================
# CONFIGURACIÓN
# ==============================================================
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

PARES = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "XAUUSD=X"]

RSI_PERIOD = 14
RSI_BUY = 50
RSI_SELL = 50

ATR_PERIOD = 14
ATR_TP_MULT = 2.0   # ATR x2
ATR_SL_MULT = 1.0   # ATR x1


# ==============================================================
# RSI
# ==============================================================
def rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    roll_up = pd.Series(gain).rolling(period).mean()
    roll_down = pd.Series(loss).rolling(period).mean()

    RS = roll_up / roll_down
    return 100 - (100 / (1 + RS))


# ==============================================================
# ATR PARA TP/SL
# ==============================================================
def calcular_atr(df):
    high_low = df["High"] - df["Low"]
    high_close = abs(df["High"] - df["Close"].shift())
    low_close = abs(df["Low"] - df["Close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    atr = true_range.rolling(ATR_PERIOD).mean()
    return atr


# ==============================================================
# FUNCIÓN DE SEÑALES
# ==============================================================
def generar_senal(df, par):

    # --- FIX MULTIINDEX ---
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)

    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["RSI"] = rsi(df["Close"], RSI_PERIOD)
    df["ATR"] = calcular_atr(df)

    c = df.iloc[-1].astype(float)
    p = df.iloc[-2].astype(float)

    print(f"--- Análisis {par} ---")
    print(f"EMA20 actual: {c['EMA20']:.5f}")
    print(f"EMA50 actual: {c['EMA50']:.5f}")
    print(f"RSI actual: {c['RSI']:.2f}")
    print(f"ATR actual: {c['ATR']:.5f}")

    # Cruces
    cruce_up = p["EMA20"] <= p["EMA50"] and c["EMA20"] > c["EMA50"]
    cruce_down = p["EMA20"] >= p["EMA50"] and c["EMA20"] < c["EMA50"]

    # Velas
    vela_verde = c["Close"] > c["Open"]
    vela_roja = c["Close"] < c["Open"]

    # BUY
    if cruce_up and vela_verde and c["RSI"] > RSI_BUY:
        entry = c["Close"]
        atr_val = c["ATR"]

        tp = entry + atr_val * ATR_TP_MULT
        sl = entry - atr_val * ATR_SL_MULT

        print("SEÑAL BUY DETECTADA ✔")
        return {
            "tipo": "BUY",
            "par": par,
            "entrada": float(entry),
            "tp": float(tp),
            "sl": float(sl)
        }

    # SELL
    if cruce_down and vela_roja and c["RSI"] < RSI_SELL:
        entry = c["Close"]
        atr_val = c["ATR"]

        tp = entry - atr_val * ATR_TP_MULT
        sl = entry + atr_val * ATR_SL_MULT

        print("SEÑAL SELL DETECTADA ✔")
        return {
            "tipo": "SELL",
            "par": par,
            "entrada": float(entry),
            "tp": float(tp),
            "sl": float(sl)
        }

    print("Sin señal.")
    return None


# ==============================================================
# CORREO BONITO (HTML)
# ==============================================================
def enviar_alerta(senal):

    tipo = senal["tipo"]
    color = "green" if tipo == "BUY" else "red"

    html = f"""
    <html>
    <body>
        <h2 style="color:{color};">🔔 Señal {tipo} Detectada</h2>

        <table border="1" cellpadding="6" style="border-collapse: collapse; font-size: 14px;">
            <tr><td><b>Par:</b></td><td>{senal['par']}</td></tr>
            <tr><td><b>Entrada:</b></td><td>{senal['entrada']}</td></tr>
            <tr><td><b>Take Profit (TP):</b></td><td>{senal['tp']}</td></tr>
            <tr><td><b>Stop Loss (SL):</b></td><td>{senal['sl']}</td></tr>
        </table>

        <br>
        <p>Bot EMA20/EMA50 + RSI + Vela + ATR (TP/SL)</p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = f"🚀 Señal {tipo} - {senal['par']}"

    msg.attach(MIMEText(html, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, EMAIL_TO, msg.as_string())
        server.quit()
        print("Correo enviado ✔")
    except Exception as e:
        print(f"Error enviando correo: {e}")


# ==============================================================
# MAIN LOOP
# ==============================================================
if __name__ == "__main__":
    print("=== BOT EJECUTANDO (YFINANCE + TP/SL + HTML) ===\n")

    for par in PARES:
        print(f"\nDescargando datos de {par}...")
        df = yf.download(par, interval="1h", period="7d")

        if df is None or len(df) < 50:
            print("Datos insuficientes.")
            continue

        senal = generar_senal(df, par)

        if senal:
            enviar_alerta(senal)
