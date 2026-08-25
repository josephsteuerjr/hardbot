@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"

where pyw >nul 2>&1
if errorlevel 1 goto console

start "" pyw -3 -m registry_cycle.manual_app
exit /b 0

:console
py -3 -m registry_cycle.manual_app
if errorlevel 1 pause
