#!/usr/bin/env bash
# Inicialização do monitor de telão.
# Execute uma vez para instalar dependências, depois use diretamente com python monitor.py.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Cria virtualenv se não existir
if [ ! -d ".venv" ]; then
    echo "[setup] Criando virtualenv..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "[setup] Instalando dependências Python..."
pip install -q -r requirements.txt

echo "[setup] Instalando browser Chromium do Playwright..."
playwright install chromium

if [ ! -f ".env" ]; then
    echo ""
    echo "⚠  Arquivo .env não encontrado!"
    echo "   Copie o exemplo e configure antes de continuar:"
    echo "   cp .env.example .env && nano .env"
    exit 1
fi

echo "[monitor] Iniciando..."
python monitor.py
