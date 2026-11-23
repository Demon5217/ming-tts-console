#!/bin/bash
set -eu
cd /opt/sglang
. .venv/bin/activate
export CUDA_HOME=/opt/sglang/.venv/lib/python3.12/site-packages/nvidia/cu13
export PATH=$CUDA_HOME/bin:$PATH
export SGLANG_OMNI_STARTUP_TIMEOUT=1800
exec sgl-omni serve --model-path /opt/models/Ming-3.0-tts-0.5B --host 0.0.0.0 --port 8000 --mem-fraction-static 0.95
