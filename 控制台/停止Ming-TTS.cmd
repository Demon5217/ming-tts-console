@echo off
chcp 65001 >nul
title Ming TTS Stop
cd /d "%~dp0"

"C:\Windows\System32\wsl.exe" -d Ubuntu-22.04 -e bash /opt/sglang/start_ming.sh stop
"C:\Windows\System32\wsl.exe" --terminate Ubuntu-22.04

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo.
echo Stopped. Console page server on port 8765 also stopped.
echo Press any key to close.
pause >nul
