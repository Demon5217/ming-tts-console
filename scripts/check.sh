#!/usr/bin/env bash
# 仓库自检：语法 + 个人路径扫描。本地和 CI 都能跑。
#   bash scripts/check.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FAIL=0
PY="${PYTHON_BIN:-python3}"

echo "== 1/3 Python 语法 =="
for f in 控制台/ming-service-host.py 服务端/serve_official.py; do
  if "$PY" -m py_compile "$f" 2>/tmp/pyerr; then
    echo "ok   $f"
  else
    echo "FAIL $f"; cat /tmp/pyerr; FAIL=1
  fi
done

echo "== 2/3 Shell 语法 =="
for f in 控制台/start.sh 控制台/stop.sh 控制台/config.sh \
         服务端/启动脚本/start_ming.sh 服务端/启动脚本/ming_tts_service.sh; do
  if bash -n "$f" 2>/tmp/sherr; then
    echo "ok   $f"
  else
    echo "FAIL $f"; cat /tmp/sherr; FAIL=1
  fi
done

echo "== 3/3 个人路径扫描 =="
PATTERNS='小明demon|161347251|C:\\Users|/mnt/c/|AppData\\Roaming'
# 只扫 git 跟踪的文件，避免把本地日志/调试脚本里的路径误报成发布内容
if git rev-parse --git-dir >/dev/null 2>&1; then
  HITS="$(git ls-files -z | xargs -0 grep -n -I -E "$PATTERNS" 2>/dev/null \
          | grep -v '^scripts/check.sh' || true)"
else
  HITS="$(grep -rn -I -E "$PATTERNS" . --exclude-dir=.git --exclude='check.sh' 2>/dev/null || true)"
fi
if [ -n "$HITS" ]; then
  echo "发现疑似个人路径，请改成配置项："
  echo "$HITS"
  FAIL=1
else
  echo "ok   未发现个人路径"
fi

echo
if [ "$FAIL" -ne 0 ]; then
  echo "自检未通过"
  exit 1
fi
echo "自检通过"
