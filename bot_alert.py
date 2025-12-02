import yfinance as yf
import pandas as pd
import numpy as np

# ===============================
#   NORMALIZADOR UNIVERSAL 1D
# ===============================
def to_1d(series):
    """
    Convierte cualquier objeto (Series, ndarray o DataFrame de 1 columna)
    en un vector 1D limpio y compatible.
    """
    # DataFrame con 1 columna → Serie 1D
    if isinstance(series, pd.DataFrame):
        return series.iloc[:, 0].astype(float)

    # Convertir a Serie
    s = pd.Series(series)

    # Si tiene más de una dimensión → aplastar
    if len(s.shape) > 1:
        return s.squeeze().astype(float)

    return s.astype(float)


# ===============================
#             RSI
# ===============================
def rsi(series, period=14):
    series = to_1d(series)

    delta = series.diff()

    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)

    gain = pd.Series(gain).rolling(period).mean()
    loss = pd.Series(loss).rolling(period).mean()

    rs = gain / loss
    return 100 - (100 / (1 + rs))


# ===============================
#      GENERADOR DE SEÑALES
# ===============================
def obtener_senal(df):
    # Normalizar a 1D garantizado
    close = to_1d(df["close"])
    open_ = to_1d(df["open"])

    df["rsi"] = rsi(close)

    ultima = df.iloc[-1]

    # Ejemplo simple de reglas (puedes ajustar)
    if ultima["rsi"] < 30:
        return {
            "senal": "COMPRA",
            "rsi": ultima["rsi"],
            "precio": ultima["close"]
        }
    elif ultima["rsi"] > 70:
        return {
            "senal": "VENTA",
            "rsi": ultima["rsi"],
            "precio": ultima["close"]
        }
    else:
        return {
            "senal": "SIN SEÑAL",
            "rsi": ultima["rsi"],
            "precio": ultima["close"]
        }


# ===============================
#       EJECUCIÓN PRINCIPAL
# ===============================
def main():
    ticker = "EURUSD=X"

    datos = yf.download(ticker, interval="15m", period="7d")

    if datos.empty:
        print("Error: No se obtuvieron datos.")
        return

    datos = datos.rename(columns=str.lower)

    senal = obtener_senal(datos)

    print("Resultado:")
    print(senal)


if __name__ == "__main__":
    main()
