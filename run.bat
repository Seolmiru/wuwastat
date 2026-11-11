@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto run

echo [wuwastat] First run: creating virtual environment and installing packages...
python -m venv .venv
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

:run
start "" ".venv\Scripts\pythonw.exe" -m wuwastat
exit /b 0

:error
echo.
echo [wuwastat] Setup failed. Make sure Python 3.11 or newer is installed and on PATH.
pause
exit /b 1
