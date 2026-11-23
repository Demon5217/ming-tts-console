@echo off
rem ============================================================
rem  Ming console local config - only edit these two variables.
rem  All other scripts call this file.
rem ============================================================

rem  Python interpreter used to run ming-service-host.py
if not defined PYTHON_EXE set "PYTHON_EXE=C:\Program Files\Python313\python.exe"

rem  WSL distro name (as shown by: wsl -l -v)
if not defined MING_WSL_DISTRO set "MING_WSL_DISTRO=Ubuntu-22.04"

rem  Legacy alias kept for older scripts
set "WSL_DISTRO=%MING_WSL_DISTRO%"
