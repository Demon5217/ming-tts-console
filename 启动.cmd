@echo off
"%SystemRoot%\System32\chcp.com" 65001 >nul 2>nul
title Ming TTS Console
cd /d "%~dp0"

rem 唯一的日常入口：起管家（8766，同时提供页面），然后自动开浏览器。
rem 引擎不在这里起，全部由页面上的按钮控制。

if not exist "%~dp0控制台\启动Ming控制台.cmd" (
  echo [ERROR] 找不到 控制台\启动Ming控制台.cmd
  echo 请确认当前目录是本项目根目录。
  pause
  exit /b 1
)

call "%~dp0控制台\启动Ming控制台.cmd"
