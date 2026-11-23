@echo off
rem ============================================================
rem  Ming 控制台本地配置：只改这两个变量，其它脚本都会 call 本文件
rem ============================================================
rem  Python 解释器（用来跑 ming-service-host.py）
if not defined PYTHON_EXE set "PYTHON_EXE=C:\Program Files\Python313\python.exe"

rem  WSL 发行版名（wsl -l -v 里看到的名字）
if not defined MING_WSL_DISTRO set "MING_WSL_DISTRO=Ubuntu-22.04"

rem 旧脚本用的别名，保持一致
set "WSL_DISTRO=%MING_WSL_DISTRO%"
