@echo off
chcp 65001 >nul
title Ming TTS Stop
cd /d "%~dp0"
call "%~dp0config.cmd"

"C:\Windows\System32\wsl.exe" -d "%MING_WSL_DISTRO%" -e bash /opt/sglang/start_ming.sh stop
"C:\Windows\System32\wsl.exe" --terminate "%MING_WSL_DISTRO%"

powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo.
echo Stopped. WSL distro %MING_WSL_DISTRO% terminated; console page server on 8765 also stopped.
echo Press any key to close.
pause >nul
