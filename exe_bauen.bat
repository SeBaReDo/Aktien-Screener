@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean --windowed --name "Aktien Screener" trading_scanner.py
echo.
echo Die EXE befindet sich danach im Ordner dist\Aktien Screener.
pause
