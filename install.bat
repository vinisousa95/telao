@echo off
chcp 65001 >nul
setlocal

echo ============================================================
echo  Instalacao do Monitor de Telao
echo ============================================================
echo.

cd /d "%~dp0"

:: Tenta encontrar o Python por varios comandos possiveis
set PYTHON_CMD=

python --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=python & goto :found_python )

py --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=py & goto :found_python )

python3 --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=python3 & goto :found_python )

py -3.14 --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=py -3.14 & goto :found_python )

py -3.12 --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=py -3.12 & goto :found_python )

py -3.10 --version >nul 2>&1
if not errorlevel 1 ( set PYTHON_CMD=py -3.10 & goto :found_python )

:: Busca direto no AppData (instalacao via Microsoft Store / Python Manager)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" (
        set PYTHON_CMD="%%D\python.exe"
        goto :found_python
    )
)

echo [ERRO] Python nao encontrado!
echo.
echo Feche este terminal, abra um NOVO cmd e tente novamente.
echo Se o problema persistir, reinicie o computador.
pause
exit /b 1

:found_python
echo [1/4] Python encontrado: %PYTHON_CMD%
%PYTHON_CMD% --version

:: Cria virtualenv
echo.
echo [2/4] Criando ambiente virtual...
if not exist ".venv" (
    %PYTHON_CMD% -m venv .venv
) else (
    echo     Ambiente virtual ja existe, pulando...
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERRO] Falha ao criar o ambiente virtual.
    pause
    exit /b 1
)

:: Instala dependências
echo.
echo [3/4] Instalando dependencias Python...
.venv\Scripts\pip install -q -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)

:: Instala Chromium do Playwright
echo.
echo [4/4] Instalando browser Chromium (pode demorar alguns minutos)...
.venv\Scripts\playwright install chromium
if errorlevel 1 (
    echo [ERRO] Falha ao instalar o Chromium.
    pause
    exit /b 1
)

:: Verifica .env
echo.
if not exist ".env" (
    copy .env.example .env >nul
    echo [AVISO] Arquivo .env criado a partir do exemplo.
    echo         Preencha os dados antes de iniciar o monitor:
    echo           - TARGET_URL  : pagina que o telao deve mostrar
    echo           - LOGIN_URL   : pagina de login do sistema
    echo           - TELAO_USERNAME e TELAO_PASSWORD
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
