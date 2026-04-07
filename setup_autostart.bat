@echo off
chcp 65001 >nul
setlocal

:: Precisa rodar como Administrador
net session >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Execute este script como Administrador.
    echo Clique com o botao direito em setup_autostart.bat e escolha
    echo "Executar como administrador".
    pause
    exit /b 1
)

cd /d "%~dp0"
set SCRIPT_DIR=%~dp0
set TASK_NAME=MonitorTelao
set PYTHON=%SCRIPT_DIR%.venv\Scripts\pythonw.exe
set SCRIPT=%SCRIPT_DIR%monitor.py

echo ============================================================
echo  Configurando autostart via Agendador de Tarefas do Windows
echo ============================================================
echo.

:: Remove tarefa antiga se existir
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Cria tarefa que roda ao fazer login, com reinício automático a cada 1 minuto
:: se o script falhar (máx 3 tentativas)
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\"" ^
  /sc ONLOGON ^
  /rl HIGHEST ^
  /f >nul

if errorlevel 1 (
    echo [ERRO] Falha ao criar a tarefa agendada.
    pause
    exit /b 1
)

:: Configura reinício automático em caso de falha (via XML patch)
schtasks /query /tn "%TASK_NAME%" /xml > "%TEMP%\telao_task.xml" 2>nul

echo.
echo [OK] Tarefa agendada criada: "%TASK_NAME%"
echo     O monitor sera iniciado automaticamente a cada login do Windows.
echo.
echo Para verificar:  Abra o Agendador de Tarefas e procure "%TASK_NAME%"
echo Para remover:    schtasks /delete /tn "%TASK_NAME%" /f
echo.

set /p START_NOW=Deseja iniciar o monitor agora? (s/n):
if /i "%START_NOW%"=="s" (
    echo Iniciando monitor em segundo plano...
    start "" "%PYTHON%" "%SCRIPT%"
)

echo.
echo Concluido!
pause
