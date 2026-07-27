@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m registry_cycle gui
if errorlevel 1 (
  echo.
  echo Контур завершился с ошибкой.
  pause
)
