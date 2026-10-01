@echo off
chcp 65001 >nul
setlocal
title 回滚 - WorkBuddy 客户端登录态修复
set "DIR=%~dp0"
set "EXE=%DIR%trae-work-assistant.exe"
set "BAK="
for %%F in ("%DIR%trae-work-assistant.exe.bak-*-preClientStatusPatch") do set "BAK=%%~fF"

echo ==================================================
echo   WorkBuddy 面板「客户端未登录」修复 —— 回滚
echo ==================================================
echo.

if not exist "%EXE%" (
  echo [错误] 找不到 "%EXE%"
  echo         未改动任何文件。
  pause
  exit /b 1
)

if "%BAK%"=="" (
  echo [错误] 找不到备份文件 trae-work-assistant.exe.bak-*-preClientStatusPatch
  echo         回滚中止，未改动任何文件。
  pause
  exit /b 1
)

echo 备份文件: %BAK%
echo 目标文件: %EXE%
echo.

tasklist /FI "IMAGENAME eq trae-work-assistant.exe" 2>nul | find /I "trae-work-assistant.exe" >nul
if not errorlevel 1 (
  echo [提示] 助手正在运行，请先在系统托盘完全退出，然后重新运行本脚本。
  pause
  exit /b 1
)

copy /Y "%BAK%" "%EXE%" >nul
if errorlevel 1 (
  echo [错误] 覆盖失败，请检查文件权限。
  pause
  exit /b 1
)

echo [完成] 已回滚为原版，重新打开助手即可。
echo.
pause
