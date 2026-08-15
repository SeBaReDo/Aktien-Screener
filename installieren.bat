@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installiere benoetigte Komponenten ...
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation fehlgeschlagen. Bitte pruefen, ob Python installiert ist.
  pause
  exit /b 1
)
echo.
echo Installation abgeschlossen.
pause
