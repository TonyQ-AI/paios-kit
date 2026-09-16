@echo off
rem PAIOS-Kit one-click installer (double-click friendly, ASCII only)
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo.
  echo Please install Python 3.10 or newer:
  echo   1. Open https://www.python.org/downloads/
  echo   2. Download and run the installer
  echo   3. IMPORTANT: check the box "Add Python to PATH" on the first screen
  echo Step-by-step help: open TROUBLESHOOTING.md - section 1.
  echo.
  pause
  exit /b 1
)
echo Installing PAIOS-Kit ... (see messages below)
echo.
python install.py %*
echo.
echo ----------------------------------------
echo Finished. If something went wrong above,
echo open TROUBLESHOOTING.md and find your error message there.
echo.
pause
