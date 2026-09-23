@echo off
REM Lanca a interface web do viral-clipper (servidor local + browser).
REM Mantenha esta janela aberta enquanto usar a UI; feche-a para parar o servidor.
setlocal
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
call ".venv\Scripts\python.exe" web\server.py
pause
