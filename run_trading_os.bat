@echo off
REM Start the Trading OS ^& Journal. Creates .venv and installs dependencies on first run.
REM Usage: run_trading_os.bat [--demo] [--port 8765] [--no-browser]
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment in .venv ...
  python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --disable-pip-version-check -r trading_os\requirements.txt || goto :error
python run_trading_os.py %*
goto :eof
:error
echo Failed to start Trading OS. Is Python 3.10+ installed and on PATH?
pause
exit /b 1
