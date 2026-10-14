@echo off
REM Reinicia a interface web do viral-clipper (porta 7755).
REM Mata quem estiver ouvindo, espera liberar e sobe de novo MINIMIZADO.
REM Feche a janela minimizada para parar; esta aqui pode ser fechada.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERRO: nao existe .venv nesta pasta:
  echo.
  echo   %~dp0
  echo.
  pause
  exit /b 1
)

echo Parando o servidor na porta 7755...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":7755 .*LISTENING"') do (
  taskkill /F /PID %%p >nul 2>&1
)

set /a tentativas=0
:aguardar
netstat -ano | findstr /r /c:":7755 .*LISTENING" >nul 2>&1
if errorlevel 1 goto subir
set /a tentativas+=1
if %tentativas% geq 10 (
  echo ERRO: a porta 7755 continua ocupada.
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
goto aguardar

:subir
start "viral-clipper web UI" /min ".venv\Scripts\python.exe" web\server.py
echo.
echo Servidor reiniciado: http://127.0.0.1:7755/
echo Janela minimizada na barra de tarefas; feche AQUELA para parar.
