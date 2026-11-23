@echo off
chcp 65001 >nul
title Ming TTS Console
cd /d "%~dp0"

rem 只起「服务管家」（8766）：它同时提供控制台页面，并负责页面上的引擎启停。
rem 引擎不在开机/启动时拉起，全部由页面按钮控制。
powershell -NoProfile -Command ^
  "if (Test-NetConnection 127.0.0.1 -Port 8766 -InformationLevel Quiet -WarningAction SilentlyContinue) { Start-Process 'http://127.0.0.1:8766/ming-console.html' } else { Start-Process -FilePath 'C:\Program Files\Python313\python.exe' -ArgumentList '%~dp0ming-service-host.py','--open' -WindowStyle Hidden }"
