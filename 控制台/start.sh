#!/usr/bin/env bash
# 启动 Ming 控制台（Linux 原生，不需要 WSL）
#
#   ./start.sh                 # 只起管家+页面，引擎在页面上点按钮启停
#   ./start.sh official        # 起管家，再拉起完整模式（8010）
#   ./start.sh fast            # 起管家，再拉起快速模式（8000）
#
# Windows 请用「启动Ming控制台.cmd」，本脚本不适用。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# shellcheck source=config.sh
[ -f ./config.sh ] && . ./config.sh || true
: "${PYTHON_BIN:=python3}"
: "${MING_HOST_PORT:=8766}"

PIDFILE="$HERE/.ming-host.pid"
LOG="$HERE/.ming-host.log"
URL="http://127.0.0.1:${MING_HOST_PORT}/ming-console.html"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[ERROR] 找不到 Python：$PYTHON_BIN"
  echo "请编辑 $HERE/config.sh 里的 PYTHON_BIN"
  exit 1
fi

# 已有实例就先收掉，避免端口占用
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "[1/3] 管家已在运行（pid $(cat "$PIDFILE")），复用"
else
  echo "[1/3] 启动服务管家（端口 ${MING_HOST_PORT}）…"
  nohup "$PYTHON_BIN" "$HERE/ming-service-host.py" >"$LOG" 2>&1 &
  echo $! >"$PIDFILE"
fi

# 等端口通
echo -n "[2/3] 等待页面就绪"
for _ in $(seq 1 30); do
  if "$PYTHON_BIN" - "$MING_HOST_PORT" <<'PY' >/dev/null 2>&1
import socket, sys
socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1).close()
PY
  then
    echo " ok"; break
  fi
  echo -n "."; sleep 0.5
done

ENGINE="${1:-}"
if [ -n "$ENGINE" ]; then
  case "$ENGINE" in
    official|fast) ;;
    *) echo "[ERROR] 引擎只能是 official 或 fast"; exit 1 ;;
  esac
  echo "[3/3] 请求启动引擎 $ENGINE（首次冷启动可能很久，进度看 $LOG）…"
  # 用 Python 发请求，避免依赖 curl；没有超时，冷启动要等多久就等多久
  if ! "$PYTHON_BIN" - "$MING_HOST_PORT" "$ENGINE" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

port, engine = sys.argv[1], sys.argv[2]
url = "http://127.0.0.1:%s/start?engine=%s" % (port, urllib.parse.quote(engine))
req = urllib.request.Request(url, method="POST")
try:
    with urllib.request.urlopen(req) as r:
        body = r.read().decode("utf-8", "replace")
except Exception as e:
    print("[WARN] 启动请求失败：%s" % e)
    sys.exit(1)

try:
    data = json.loads(body)
except ValueError:
    print(body)
    sys.exit(0)

if data.get("ok"):
    print("引擎 %s 已就绪" % data.get("engine", engine))
else:
    print("[WARN] %s" % data.get("error", body))
    sys.exit(1)
PY
  then
    echo "启动未成功，可稍后在页面上重试"
  fi
else
  echo "[3/3] 跳过引擎启动（可在页面上点按钮）"
fi

echo
echo "控制台页面：$URL"
echo "日志：$LOG"
echo "停止：$HERE/stop.sh"

if [ -n "${DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
fi
