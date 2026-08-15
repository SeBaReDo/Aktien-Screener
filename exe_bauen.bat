@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean --windowed --name "Aktien App" --add-data "csi300_fallback.csv;." --add-data "marktdaten;marktdaten" trading_scanner.py
echo.
echo Die EXE befindet sich danach im Ordner dist\Aktien App.
echo Wichtig: der Ordner "marktdaten" (Zentralbank-/Wahltermine) muss neben der EXE liegen bzw. wird mitgebaut.
pause
