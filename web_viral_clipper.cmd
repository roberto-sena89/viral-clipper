@echo off
REM Lanca a interface web do viral-clipper (servidor local + browser).
REM Mantenha esta janela aberta enquanto usar a UI; feche-a para parar o servidor.
REM
REM O .venv e por checkout. Se voce tem mais de uma copia do repo (um worktree,
REM por exemplo) cada uma precisa do seu -- veja o guard abaixo.
setlocal
cd /d "%~dp0"
set PYTHONUNBUFFERED=1

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo ERRO: nao existe .venv nesta pasta.
  echo.
  echo   %~dp0
  echo.
  echo O servidor NAO foi iniciado. Se voce ja tem um servidor no ar em outra
  echo copia do repo, ele continua respondendo e a interface parece "a antiga".
  echo.
  echo Crie o ambiente assim ^(uma vez^):
  echo.
  echo   py -3.13 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  echo.
  echo O venv do checkout principal serve de referencia: e' 3.13 com
  echo curl_cffi e opencv-python-headless^<5 alem do requirements-dev.
  echo.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" web\server.py
pause
