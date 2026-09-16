@echo off
rem PAIOS-Kit uninstaller (removes registration, keeps your data)
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. See TROUBLESHOOTING.md section 1.
  pause
  exit /b 1
)
python uninstall.py %*
echo.
echo Your data folder (default C:\Users\you\paios) is KEPT.
echo Delete it manually only if you no longer need your session archive and atoms.
echo.
pause
