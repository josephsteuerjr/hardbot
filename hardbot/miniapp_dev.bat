@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запускаю миниапп в дев-режиме (синтетика, изолированная БД)...
python miniapp_dev.py
pause
