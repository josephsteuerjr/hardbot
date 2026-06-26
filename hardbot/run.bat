@echo off
REM ==== Пульт «Контур · Выезды» — без Docker (двойной клик) ====
REM Реестр/БД остаются на этом компьютере (рядом со скриптом). Бота запускает пульт.
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [*] Первый запуск: создаю окружение...
  python -m venv .venv || (echo [!] Не найден python. Установите Python 3.12+ и поставьте галочку "Add to PATH". & pause & exit /b 1)
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip >nul
  echo [*] Ставлю зависимости...
  pip install -r requirements.txt || (echo [!] Не удалось поставить зависимости. & pause & exit /b 1)
) else (
  call ".venv\Scripts\activate.bat"
)

set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
echo [*] Открываю пульт. Бота запускайте кнопкой внутри.
python gui.py
pause
