#!/usr/bin/env bash
set -o errexit  # Detiene el script si hay error

echo "Instalando dependencias del sistema..."
python3 -m pip install --upgrade pip

echo "Instalando dependencias del proyecto..."
python3 -m pip install -r requirements.txt

echo "Ejecutando bot..."
python3 ./bot_alerts.py
