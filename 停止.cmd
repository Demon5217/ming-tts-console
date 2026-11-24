@echo off
"%SystemRoot%\System32\chcp.com" 65001 >nul 2>nul
title Ming TTS Console - stop
cd /d "%~dp0"
call "%~dp0控制台\config.cmd"

if not defined MING_SGLANG_DIR set "MING_SGLANG_DIR=/opt/sglang"
if not defined MING_HOST_PORT set "MING_HOST_PORT=8766"

echo ========================================
echo   Ming TTS Console - 全部停止
echo ========================================
echo.

rem 注意：wsl.exe 的 -d 参数不要加引号，加了会把引号当成发行版名的一部分，报 WSL_E_DISTRO_NOT_FOUND
echo [1/4] 停快速模式（SGLang / %MING_SGLANG_DIR%）...
"%SystemRoot%\System32\wsl.exe" -d %MING_WSL_DISTRO% -e bash -lc "test -f '%MING_SGLANG_DIR%/start_ming.sh' && bash '%MING_SGLANG_DIR%/start_ming.sh' stop || echo '未找到 start_ming.sh，跳过快速模式'"

echo [2/4] 停完整模式（systemd: ming-official）...
"%SystemRoot%\System32\wsl.exe" -d %MING_WSL_DISTRO% -e bash -lc "if systemctl is-active --quiet ming-official 2>/dev/null; then if systemctl stop ming-official 2>/dev/null; then echo '完整模式已停止'; else echo '需要权限，请手动执行: sudo systemctl stop ming-official'; fi; else echo '完整模式本来就没在跑'; fi"

echo [3/4] 停 ASR（8011，它跟着管家走）...
"%SystemRoot%\System32\wsl.exe" -d %MING_WSL_DISTRO% -e bash -lc "if systemctl is-active --quiet ming-asr 2>/dev/null; then if systemctl stop ming-asr 2>/dev/null; then echo 'ASR 已停止'; else echo '需要权限，请手动执行: sudo systemctl stop ming-asr'; fi; else echo 'ASR 本来就没在跑'; fi"

echo [4/4] 结束管家进程（端口 %MING_HOST_PORT%）...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Get-NetTCPConnection -LocalPort %MING_HOST_PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Start-Sleep -Milliseconds 600; if (Test-NetConnection 127.0.0.1 -Port %MING_HOST_PORT% -InformationLevel Quiet -WarningAction SilentlyContinue) { '仍然在监听 ' + %MING_HOST_PORT% + '，可能没有权限结束进程，请用任务管理器关闭 python.exe' } else { '已停止，端口 ' + %MING_HOST_PORT% + ' 已释放' }"

rem 管家强杀不会走 atexit，这里补一刀：放掉它持有的常驻会话，让 WSL 照常空闲休眠
"%SystemRoot%\System32\wsl.exe" -d %MING_WSL_DISTRO% -e bash -lc "pkill -f 'sleep infinity' 2>/dev/null || true"

echo.
echo 提示：引擎的显存已释放；管家持有的 WSL 常驻会话已放掉，WSL 会照常空闲休眠。
echo Press any key to close.
pause >nul
