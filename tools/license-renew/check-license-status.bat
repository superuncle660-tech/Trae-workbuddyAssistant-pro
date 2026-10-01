@echo off
chcp 65001 >nul
title Trae Work 助手 授权诊断
setlocal
set "BASE=%~dp0"
set "PY=%LOCALAPPDATA%\Python\bin\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo   Trae Work 授权诊断
echo   1) exe 内嵌的公钥 / 服务器候选 + 当前凭证状态
echo   2) 凭证剩余天数
echo ============================================================
echo.

"%PY%" "%BASE%renew_license.py" --dump-info
echo.
"%PY%" "%BASE%renew_license.py" --check

echo.
echo ============================================================
echo   日志  %BASE%logs\license-renew.log
echo   告警  %BASE%logs\license-alert.json
echo   状态  %BASE%logs\license-state.json
echo ============================================================
pause
