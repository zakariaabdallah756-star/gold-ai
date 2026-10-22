@echo off
REM Start the Trading OS ^& Journal. Creates trading_os\.venv and installs dependencies on first run.
REM Usage: trading_os\run.bat [--demo] [--port 8765] [--no-browser] [--lan] [--allow-host NAME]
REM   --lan              open it from your phone / other computers on your network (http://THIS-PC-IP:8765)
REM   --allow-host NAME  also answer to a LAN name or tunnel, e.g. trading.lan or *.trycloudflare.com
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment in trading_os\.venv ...
  python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :error
python run.py %*
goto :eof
:error
echo Failed to start Trading OS. Is Python 3.10+ installed and on PATH?
pause
exit /b 1
