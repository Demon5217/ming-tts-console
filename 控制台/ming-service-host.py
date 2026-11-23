"""Ming TTS 服务管家（纯标准库，不装任何依赖）。

监听 127.0.0.1:8766：既当控制台页面的静态服务，也负责一键启停两套 TTS 引擎。
- official -> 8010（官方完整模式，显存约 3.1GB）
- fast     -> 8000（SGLang 快速模式，显存约 5.9GB）
两者显存互斥，启动一个会先停掉另一个。

接口：
  GET  /                            -> 控制台页面（同目录 ming-console.html）
  GET  /ming-console.html           -> 同上
  GET  /status                      -> {official, fast, vram_used, log}
  POST /start?engine=official|fast   -> 先做 WSL 自检，再启动引擎并等 /health 通
  POST /stop?engine=official|fast|all
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
PAGE = "ming-console.html"
GUARD = os.path.join(BASE, "wsl-guard.ps1")

PORT = 8766
# 发行版名可配置：启动脚本 config.cmd 里设置 MING_WSL_DISTRO，未设置时用默认值
DISTRO = os.environ.get("MING_WSL_DISTRO") or "Ubuntu-22.04"
WSL = ["wsl.exe", "-d", DISTRO, "-e", "bash", "-lc"]

_lock = threading.Lock()
_log = []


def say(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    _log.append(line)
    del _log[:-80]


def sh(cmd, timeout=180):
    try:
        p = subprocess.run(WSL + [cmd], capture_output=True, text=True, timeout=timeout)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        return out
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    except Exception as e:
        return "ERROR: %s" % e


def port_alive(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_health(url, secs=100):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def vram_used():
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20)
        parts = [x.strip() for x in p.stdout.strip().split(",")]
        return {"used": int(parts[0]), "free": int(parts[1])}
    except Exception:
        return {"used": -1, "free": -1}


def free_c_gb():
    try:
        import shutil
        return round(shutil.disk_usage("C:\\").free / 1073741824.0, 1)
    except Exception:
        return -1


def wsl_guard():
    """启动引擎前先自检 WSL：卡死就 wsl --shutdown 重置并拉起 keepalive。

    逻辑与 wsl-guard.ps1 一致，直接复用那个脚本，避免两处维护。
    """
    if not os.path.exists(GUARD):
        say("跳过 WSL 自检（找不到 wsl-guard.ps1）")
        return
    say("WSL 自检…")
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", GUARD, "-Distro", DISTRO],
            capture_output=True, text=True, timeout=180,
        )
        lines = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        for ln in lines[-4:]:
            t = ln.strip()
            if t:
                say("  " + t)
    except subprocess.TimeoutExpired:
        say("WSL 自检超时（180s），继续尝试启动")
    except Exception as e:
        say("WSL 自检异常：%s" % e)


def stop_engine(name):
    if name in ("official", "all"):
        sh("systemctl stop ming-official 2>/dev/null || true")
        sh('pkill -f "[s]erve_official.py" || true')
        say("已停 8010")
    if name in ("fast", "all"):
        sh("systemctl stop ming-tts 2>/dev/null || true")
        say("已停 8000")


def start_engine(name):
    """启动指定引擎；显存互斥，先停另一套。"""
    if name not in ("official", "fast"):
        return {"ok": False, "error": "未知引擎 %r" % name}
    wsl_guard()
    if name == "official":
        c_free = free_c_gb()
        say("C 盘可用 %.1fGB" % c_free)
        if 0 < c_free < 2:
            return {"ok": False, "error": "C 盘可用空间低于 2GB，已中止（按你的规则保护磁盘）"}
        stop_engine("fast")
        say("启动 8010（systemd: ming-official）…")
        sh("systemctl restart ming-official 2>&1 || true")
        # 刚停完 8000 时，8010 要 4~5 分钟才能就绪（显存回收慢），240s 会误报失败
        ok = wait_health("http://127.0.0.1:8010/health", 600)
        say("8010 %s" % ("就绪" if ok else "未就绪（冷启动最长约 5 分钟，看 /mnt/c/WSL/official_serve.log）"))
        return {"ok": ok, "engine": "official"}

    if name == "fast":
        c_free = free_c_gb()
        say("C 盘可用 %.1fGB" % c_free)
        stop_engine("official")
        time.sleep(2)
        say("启动 8000 …")
        # 快速模式冷启动 10~15 分钟（含 CUDA graph），脚本自身会等 15 分钟，
        # 所以这里的超时必须比脚本长，否则会误报 TIMEOUT
        out = sh("bash /opt/sglang/start_ming.sh", timeout=960)
        if out:
            say(out.strip()[-300:])
        ok = wait_health("http://127.0.0.1:8000/health", 1500)
        say("8000 %s" % ("就绪" if ok else "未就绪（冷启动最长约 15 分钟，看 /mnt/c/WSL/serve.log）"))
        return {"ok": ok, "engine": "fast"}

    return {"ok": False, "error": "未知引擎 %r" % name}


def status():
    return {
        "official": port_alive(8010),
        "fast": port_alive(8000),
        "vram": vram_used(),
        "c_free_gb": free_c_gb(),
        "log": _log[-12:],
    }


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _html(self, name):
        path = os.path.join(BASE, name)
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self._json({"error": "not found", "page": name}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/" + PAGE):
            self._html(PAGE)
        elif u.path == "/status":
            self._json(status())
        elif u.path == "/health":
            self._json({"status": "ok"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        engine = (q.get("engine") or [""])[0]
        if u.path == "/start":
            if not _lock.acquire(blocking=False):
                self._json({"ok": False, "error": "已有任务在执行，稍等"}, 409)
                return
            try:
                self._json(start_engine(engine))
            finally:
                _lock.release()
        elif u.path == "/stop":
            stop_engine(engine or "all")
            self._json({"ok": True, "engine": engine or "all"})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    say("服务管家已启动，端口 %d（页面同端口）" % PORT)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--open" in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d/%s" % (PORT, PAGE))).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
