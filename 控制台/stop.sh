#!/usr/bin/env bash
# 停止 Ming 控制台（Linux）
#
#   ./stop.sh          # 只停管家+页面，引擎保持运行
#   ./stop.sh --all    # 连引擎一起停（需要 systemctl / sudo 权限）
#
# Windows 请用「停止Ming-TTS.cmd」，本脚本不适用。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# shellcheck source=config.sh
[ -f ./config.sh ] && . ./config.sh || true
: "${MING_HOST_PORT:=8766}"

PIDFILE="$HERE/.ming-host.pid"

if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "停止管家 pid $PID …"
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 10); do kill -0 "$PID" 2>/dev/null || break; sleep 0.3; done
    kill -9 "$PID" 2>/dev/null || true
  else
    echo "管家未在运行（pid 文件残留），清理中"
  fi
  rm -f "$PIDFILE"
else
  echo "没有 pid 文件，尝试按端口 ${MING_HOST_PORT} 兜底清理"
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${MING_HOST_PORT}/tcp" 2>/dev/null || true
  fi
fi

if [ "${1:-}" = "--all" ]; then
  echo "停止引擎 …"
  systemctl stop ming-official 2>/dev/null || true
  pkill -f "[s]erve_official.py" 2>/dev/null || true
  systemctl stop ming-tts 2>/dev/null || true
  echo "已停 8010 / 8000"
fi

echo "完成。"
