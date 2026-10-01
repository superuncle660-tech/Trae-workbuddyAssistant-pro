@echo off
chcp 65001 >nul
title Install Trae License Auto-Renew Task
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-renew-task.ps1"
echo.
pause
