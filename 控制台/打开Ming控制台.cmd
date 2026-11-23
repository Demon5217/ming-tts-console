@echo off
chcp 65001 >nul
title Ming TTS Console
cd /d "%~dp0"

echo ========================================
echo   Ming TTS Console
echo ========================================
echo.

echo [1/5] Checking WSL health (auto reset if hung) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wsl-guard.ps1"

echo [2/5] Starting TTS service ...
"C:\Windows\System32\wsl.exe" -d Ubuntu-22.04 -e bash /opt/sglang/start_ming.sh

echo [3/5] Starting local page server ...
powershell -NoProfile -Command "if (-not (Test-NetConnection 127.0.0.1 -Port 8765 -InformationLevel Quiet -WarningAction SilentlyContinue)) { Start-Process -FilePath 'C:\Program Files\Python313\python.exe' -ArgumentList '-m','http.server','8765','--bind','127.0.0.1','--directory','%~dp0.' -WindowStyle Hidden }"

echo [4/5] Starting service manager (port 8766) ...
powershell -NoProfile -Command "if (-not (Test-NetConnection 127.0.0.1 -Port 8766 -InformationLevel Quiet -WarningAction SilentlyContinue)) { Start-Process -FilePath 'C:\Program Files\Python313\python.exe' -ArgumentList '%~dp0ming-service-host.py' -WindowStyle Hidden }"

echo [5/5] Opening console ...
powershell -NoProfile -Command "Start-Sleep -Milliseconds 1200; Start-Process 'http://127.0.0.1:8765/ming-console.html'"

echo.
echo Console page: http://127.0.0.1:8765/ming-console.html
echo Press any key to close this window (services keep running).
pause >nul
