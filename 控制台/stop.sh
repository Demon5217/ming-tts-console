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
PORTFILE="$HERE/.ming-host.port"

# 端口优先以启动时记下的为准，这样 stop.sh 不必再带一次 MING_HOST_PORT
if [ -f "$PORTFILE" ]; then
  RECORDED="$(tr -d '[:space:]' <"$PORTFILE" 2>/dev/null || true)"
  [ -n "$RECORDED" ] && MING_HOST_PORT="$RECORDED"
fi

# 按端口找监听进程并杀掉：fuser 优先，退到 lsof，再退到 ss（容器里常见没有前两个）
kill_by_port() {
  local port="$1" pids=""
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "${port}/tcp" 2>/dev/null && return 0
  fi
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    [ -n "$pids" ] && kill $pids 2>/dev/null && return 0
  fi
  if command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnp 2>/dev/null | awk -v p=":${port}" '$4 ~ p' \
            | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)"
    [ -n "$pids" ] && kill $pids 2>/dev/null && return 0
  fi
  return 1
}

STOPPED=0
if [ -f "$PIDFILE" ]; then
  PID="$(cat "$PIDFILE")"
  if kill -0 "$PID" 2>/dev/null; then
    echo "停止管家 pid $PID …"
    kill "$PID" 2>/dev/null || true
    for _ in $(seq 1 10); do kill -0 "$PID" 2>/dev/null || break; sleep 0.3; done
    kill -9 "$PID" 2>/dev/null || true
    STOPPED=1
  else
    echo "pid 文件已过期（进程 $PID 不在），按端口 ${MING_HOST_PORT} 兜底清理"
  fi
else
  echo "没有 pid 文件，按端口 ${MING_HOST_PORT} 兜底清理"
fi

if [ "$STOPPED" -eq 0 ]; then
  kill_by_port "$MING_HOST_PORT" && echo "已按端口停掉管家" || echo "端口 ${MING_HOST_PORT} 上没有监听进程"
fi

rm -f "$PIDFILE" "$PORTFILE"

if [ "${1:-}" = "--all" ]; then
  echo "停止引擎 …"
  systemctl stop ming-official 2>/dev/null || true
  pkill -f "[s]erve_official.py" 2>/dev/null || true
  systemctl stop ming-tts 2>/dev/null || true
  echo "已停 8010 / 8000"
fi

echo "完成。"
