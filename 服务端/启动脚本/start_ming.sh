#!/bin/bash
# Ming-3.0-TTS 启动/停止脚本（systemd 托管版）
# 用法: start_ming.sh        启动并等待就绪
#       start_ming.sh stop   停止
PORT="${MING_FAST_PORT:-8000}"
# 日志位置跟服务单元保持一致，可用 MING_LOG_DIR 覆盖（systemd 里用 %L 之类不方便，故显式配置）
LOG_DIR="${MING_LOG_DIR:-/var/log/ming-tts}"
LOG="$LOG_DIR/serve.log"
UNIT=ming-tts.service
MIN_FREE=4700   # 模型加载后约占用 5.6GB 显存，低于这个值必然 CUDA OOM

check() {
  curl -s -o /dev/null -m 3 -w "%{http_code}" "http://127.0.0.1:$PORT/health" 2>/dev/null
}

vram_check() {
  local free
  free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d " ")
  [ -z "$free" ] && return 0
  if [ "$free" -lt "$MIN_FREE" ]; then
    echo "显卡显存只剩 ${free}MiB，模型需要约 5.6GB（5640MiB），这样启动一定 OOM。"
    echo "占用显卡的程序："
    nvidia-smi | sed -n "/Processes:/,\$p" | grep -E ".exe|python" | sed "s/|//g;s/  */ /g"
    echo "先关掉游戏 / 视频剪辑 / 其它占卡程序，再重新启动。"
    return 1
  fi
  return 0
}

if [ "${1:-}" = "stop" ]; then
  systemctl stop "$UNIT"
  pkill -f "[s]gl-omni" 2>/dev/null
  echo "已停止服务"
  exit 0
fi

if [ "$(check)" = "200" ]; then
  echo "服务已在运行，端口 $PORT"
  echo "接口: http://127.0.0.1:$PORT/v1/audio/speech"
  exit 0
fi

vram_check || exit 1

systemctl start "$UNIT"

echo "启动中（首次约 1-3 分钟，含 CUDA graph）..."
for i in $(seq 1 300); do
  sleep 3
  if [ "$(check)" = "200" ]; then
    echo "启动成功，端口 $PORT 已就绪"
    echo "接口: http://127.0.0.1:$PORT/v1/audio/speech"
    exit 0
  fi
  if ! systemctl is-active --quiet "$UNIT"; then
    echo "服务进程退出，最后日志："
    tail -20 "$LOG" 2>/dev/null
    exit 1
  fi
done
echo "15 分钟内没等到就绪，请查日志: $LOG"
tail -10 "$LOG" 2>/dev/null
