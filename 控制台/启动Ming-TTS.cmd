@echo off
chcp 65001 >nul
title Ming TTS Start
cd /d "%~dp0"

echo ========================================
echo   Ming-3.0-TTS
echo ========================================

powershell -NoProfile -Command "if (-not (Get-Process wsl -ErrorAction SilentlyContinue)) { Start-Process -FilePath 'C:\Windows\System32\wsl.exe' -ArgumentList '-d','Ubuntu-22.04','-e','sleep','infinity' -WindowStyle Hidden }"

"C:\Windows\System32\wsl.exe" -d Ubuntu-22.04 -e bash /opt/sglang/start_ming.sh

echo.
echo Endpoint: http://127.0.0.1:8000/v1/audio/speech
echo Press any key to close (service keeps running).
pause >nul
