@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=D:\AI\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -u fatfish_bot.py
pause
