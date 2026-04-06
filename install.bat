@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo  Instalação do Monitor de Telão
echo ============================================================
echo.

cd /d "%~dp0"

:: Verifica se Python está instalado
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado!
    echo.
    echo Instale o Python 3.10 ou superior em:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANTE: marque a opcao "Add Python to PATH" durante a instalacao.
    pause
    exit /b 1
)

echo [1/4] Python encontrado:
python --version

:: Cria virtualenv
echo.
echo [2/4] Criando ambiente virtual...
if not exist ".venv" (
    python -m venv .venv
) else (
    echo     Ambiente virtual ja existe, pulando...
)

:: Instala dependências
echo.
echo [3/4] Instalando dependencias Python...
call .venv\Scripts\pip install -q -r requirements.txt

:: Instala Chromium do Playwright
echo.
echo [4/4] Instalando browser Chromium...
call .venv\Scripts\playwright install chromium

:: Verifica .env
echo.
if not exist ".env" (
    copy .env.example .env >nul
    echo [AVISO] Arquivo .env criado a partir do exemplo.
    echo         Abra o arquivo .env com um editor de texto e preencha:
    echo           - TARGET_URL
    echo           - LOGIN_URL
    echo           - TELAO_USERNAME
    echo           - TELAO_PASSWORD
    echo.
    echo Pressione qualquer tecla para abrir o .env no Bloco de Notas...
    pause >nul
    notepad .env
) else (
    echo [OK] Arquivo .env encontrado.
)

echo.
echo ============================================================
echo  Instalacao concluida!
echo  Para iniciar o monitor: run.bat
echo  Para autostart no Windows: setup_autostart.bat
echo ============================================================
pause
