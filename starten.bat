@echo off
chcp 65001 >nul
cd /d "%~dp0"
py trading_scanner.py
if errorlevel 1 pause
