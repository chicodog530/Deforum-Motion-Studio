@echo off
setlocal
cd /d "%~dp0"
echo Setting up Deforum Motion Studio in its own environment...
py -3.12 -m venv .venv 2>nul
if errorlevel 1 py -3.11 -m venv .venv 2>nul
if errorlevel 1 py -3.10 -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" (
  echo Install Python 3.12 or 3.11 from python.org, including the Python launcher.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Setup complete. Double-click run.bat to open the app.
pause
exit /b 0
:failed
echo Setup failed. Check the error above. Internet access is needed for dependencies.
pause
exit /b 1
