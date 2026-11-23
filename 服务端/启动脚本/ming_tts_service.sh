#!/bin/bash
# 快速模式（SGLang / sgl-omni）启动脚本，由 ming-tts.service 调用。
# 目录、版本号都可用环境变量覆盖，默认值对应官方部署结构。
set -eu
SGLANG_DIR="${MING_SGLANG_DIR:-/opt/sglang}"
MODEL_DIR="${MING_MODEL_DIR:-/opt/models/Ming-3.0-tts-0.5B}"
PORT="${MING_FAST_PORT:-8000}"
PY_VER="${MING_VENV_PY_VER:-python3.12}"

cd "$SGLANG_DIR"
. .venv/bin/activate
export CUDA_HOME="$SGLANG_DIR/.venv/lib/$PY_VER/site-packages/nvidia/cu13"
export PATH=$CUDA_HOME/bin:$PATH
export SGLANG_OMNI_STARTUP_TIMEOUT=1800
exec sgl-omni serve --model-path "$MODEL_DIR" --host 0.0.0.0 --port "$PORT" --mem-fraction-static 0.95
