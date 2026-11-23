#!/usr/bin/env bash
# ============================================================
#  Ming console local config (Linux) - edit these, then
#  start.sh / stop.sh 会自动 source 本文件。
# ============================================================
# 用来跑管家的 Python（3.8+）
: "${PYTHON_BIN:=python3}"

# 引擎脚本所在目录（服务端部署位置，里面应有 start_ming.sh）
: "${MING_SERVICE_ROOT:=/opt/sglang}"

# 管家 + 页面端口
: "${MING_HOST_PORT:=8766}"

export PYTHON_BIN MING_SERVICE_ROOT MING_HOST_PORT
