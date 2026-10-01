@echo off
chcp 65001 >nul
title Trae Work License Renewal
setlocal

set "BASE=%~dp0"
set "PY=%LOCALAPPDATA%\Python\bin\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo   Trae Work 授权续期  (license_guard)
echo   用激活口令换一份新的 7 天授权
echo   目标: %USERPROFILE%\.license_guard\license.dat
echo ============================================================
echo.

"%PY%" "%BASE%renew_license.py" --force
set "RC=%ERRORLEVEL%"

echo.
echo ============================================================
echo   [ExitCode %RC%]   0 = 成功   1 = 失败
echo   日志: %BASE%logs\license-renew.log
echo ============================================================
pause
exit /b %RC%
