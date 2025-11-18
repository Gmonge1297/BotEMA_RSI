import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---------------------------------------
# CONFIGURACIÓN DEL BOT
# ---------------------------------------

PARES = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "XAUUSD": "GC=F"           # Gold
}

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO", EMAIL_USER)

# ---------------------------------------
# CÁLCULO DE INDICADORES
# ---------------------------------------

def calcular_indicadores(df):
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    delta = df["close"].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    roll_up = pd.Series(gain).rolling(14).mean()
    roll_down = pd.Series(loss).rolling(14).mean()

    rs = roll_up / roll_down
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


# ---------------------------------------
# GENERAR SEÑAL
# ---------------------------------------

def generar_senal(df, par):
    df = calcular_indicadores(df)

    c = df.iloc[-1]      # vela actual
    p = df.iloc[-2]      # vela previa

    # Cruce alcista → BUY
    cruce_up = (p["ema20"] <= p["ema50"]) and (c["ema20"] > c["ema50"])

    # Cruce bajista → SELL
    cruce_down = (p["ema20"] >= p["ema50"]) and (c["ema20"] < c["ema50"])

    rsi_buy = c["rsi"] < 30
    rsi_sell = c["rsi"] > 70

    precio_actual = c["close"]

    if cruce_up and rsi_buy:
        tp = precio_actual * 1.0020
        sl = precio_actual * 0.9980
        return "BUY", precio_actual, tp, sl

    if cruce_down and rsi_sell:
        tp = precio_actual * 0.9980
        sl = precio_actual * 1.0020
        return "SELL", precio_actual, tp, sl

    return None


# ---------------------------------------
# ENVIAR CORREO HTML
# ---------------------------------------

def enviar_correo_html(par, senal, precio, tp, sl):
    mensaje = MIMEMultipart("alternative")
    mensaje["Subject"] = f"📌 Señal Detectada: {par} - {senal}"
    mensaje["From"] = EMAIL_USER
    mensaje["To"] = EMAIL_TO

    html = f"""
    <html>
        <body>
            <h2>📈 Señal detectada en {par}</h2>
            <p><b>Tipo:</b> {senal}</p>
            <p><b>Precio actual:</b> {precio:.5f}</p>
            <p><b>Take Profit:</b> {tp:.5f}</p>
            <p><b>Stop Loss:</b> {sl:.5f}</p>
            <br>
            <p>Bot EMA20/EMA50 + RSI<br>
            <small>Notificación automática</small></p>
        </body>
    </html>
    """

    mensaje.attach(MIMEText(html, "html"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, EMAIL_TO, mensaje.as_string())
        server.quit()
        print(f"📧 Email enviado para {par}")
    except Exception as e:
        print("❌ Error enviando correo:", e)


# ---------------------------------------
# PROCESO PRINCIPAL
# ---------------------------------------

print("\n=== BOT EJECUTANDO (YFINANCE + TP/SL + HTML) ===\n")

for par, ticker in PARES.items():
    print(f"\nDescargando datos de {ticker}...")

    df = yf.download(ticker, interval="1h", period="7d")

    if df.empty:
        print("❌ Sin datos. Se omite.")
        continue

    # NORMALIZACIÓN DE COLUMNAS
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['_'.join(col).strip() for col in df.columns.values]

    df.columns = df.columns.str.lower()

    if "close" not in df.columns:
        print("❌ No existe columna CLOSE en los datos. Se omite.")
        continue

    senal = generar_senal(df, par)

    if senal:
        tipo, precio, tp, sl = senal
        print(f"✔ Señal detectada: {par} → {tipo}")
        enviar_correo_html(par, tipo, precio, tp, sl)
    else:
        print(f"— No hay señales en {par}")
