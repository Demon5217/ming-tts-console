@echo off
"%SystemRoot%\System32\chcp.com" 65001 >nul 2>nul
title Ming TTS Console
cd /d "%~dp0"
call "%~dp0config.cmd"

rem 只起「服务管家」（默认 8766，改 config.cmd 的 MING_HOST_PORT 即可）：
rem 它同时提供控制台页面，并负责页面上的引擎启停。
rem 引擎不在开机/启动时拉起，全部由页面按钮控制。

if not exist "%PYTHON_EXE%" (
  echo [ERROR] 找不到 Python: %PYTHON_EXE%
  echo 请编辑 %~dp0config.cmd 里的 PYTHON_EXE
  pause
  exit /b 1
)

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command ^
  "if (Test-NetConnection 127.0.0.1 -Port %MING_HOST_PORT% -InformationLevel Quiet -WarningAction SilentlyContinue) { Start-Process 'http://127.0.0.1:%MING_HOST_PORT%/ming-console.html' } else { Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList '%~dp0ming-service-host.py','--open' -WindowStyle Hidden }"
