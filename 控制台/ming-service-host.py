"""Ming TTS 服务管家（纯标准库，不装任何依赖）。

监听 127.0.0.1:8766：既当控制台页面的静态服务，也负责一键启停两套 TTS 引擎。
- official -> 8010（官方完整模式，显存约 3.1GB）
- fast     -> 8000（SGLang 快速模式，显存约 5.9GB）
两者显存互斥，启动一个会先停掉另一个。

接口：
  GET  /                            -> 控制台页面（同目录 ming-console.html）
  GET  /ming-console.html           -> 同上
  GET  /status                      -> {official, fast, asr, vram, c_free_gb, log}
  POST /start?engine=official|fast   -> 先做 WSL 自检，再启动引擎并等 /health 通
  POST /stop?engine=official|fast|all

ASR（可选，8011）不归页面管，跟着管家进程走：管家起来就在后台拉起，
管家退出（Ctrl+C / SIGTERM / 正常退出）时一起停掉。它跑的是 faster-whisper
small（device=cpu, int8），只吃内存不吃显存，所以和两套引擎不冲突。
"""

import atexit
import json
import os
import shlex
import signal
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

PORT = int(os.environ.get("MING_HOST_PORT") or 8766)
IS_WIN = sys.platform == "win32"
# 引擎脚本所在目录（服务端部署位置），可用环境变量 MING_SERVICE_ROOT 覆盖
SERVICE_ROOT = os.environ.get("MING_SERVICE_ROOT") or "/opt/sglang"
# 可选 ASR 服务（faster-whisper small，跑 CPU），生命周期跟着管家进程
ASR_UNIT = "ming-asr"
ASR_PORT = int(os.environ.get("MING_ASR_PORT") or 8011)
# 发行版名可配置：启动脚本 config.cmd 里设置 MING_WSL_DISTRO，未设置时用默认值
DISTRO = os.environ.get("MING_WSL_DISTRO") or "Ubuntu-22.04"
# Windows 走 WSL 执行 shell；Linux 直接本地 bash
WSL = ["wsl.exe", "-d", DISTRO, "-e", "bash", "-lc"] if IS_WIN else ["bash", "-lc"]

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
    """模型/缓存所在盘的剩余空间（Windows 看 C 盘，Linux 看根目录）。"""
    try:
        import shutil
        target = "C:\\" if IS_WIN else "/"
        return round(shutil.disk_usage(target).free / 1073741824.0, 1)
    except Exception:
        return -1


def wsl_guard():
    """Windows 启动引擎前先自检 WSL：卡死就 wsl --shutdown 重置并拉起 keepalive。

    逻辑与 wsl-guard.ps1 一致，直接复用那个脚本，避免两处维护。
    Linux 上引擎是本机进程，没有 WSL 这一层，直接跳过。
    """
    if not IS_WIN:
        say("Linux：跳过 WSL 自检")
        return
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


def sh_ok(cmd):
    """在目标 shell 里跑一条判断命令，输出含 yes 视为成立。"""
    return "yes" in (sh(cmd, timeout=30) or "")


def has_start_script():
    return sh_ok("test -f %s && echo yes || echo no" % shlex.quote(os.path.join(SERVICE_ROOT, "start_ming.sh")))


def has_systemctl():
    return sh_ok("command -v systemctl >/dev/null 2>&1 && echo yes || echo no")


def has_official_unit():
    """systemctl 在、但 unit 没注册时，restart 只会静默失败，必须提前拦住。"""
    return sh_ok("systemctl cat ming-official >/dev/null 2>&1 && echo yes || echo no")


def systemd_running():
    """容器里 systemctl 命令在，但 systemd 不是 PID 1，重启服务只会得到 offline。

    只有 running / degraded / starting 才算真的能用。
    """
    out = (sh("systemctl is-system-running 2>&1 || true", timeout=30) or "").strip()
    first = out.split()[0].lower() if out.split() else ""
    return first in ("running", "degraded", "starting")


def has_asr_unit():
    return sh_ok("systemctl cat %s >/dev/null 2>&1 && echo yes || echo no" % ASR_UNIT)


def asr_active():
    return port_alive(ASR_PORT)


def start_asr():
    """管家起来时顺带拉起 ASR；起不来只记日志，绝不影响引擎与页面。

    放在后台线程里做（模型暖机要十几秒），避免拖慢页面打开。
    """
    if asr_active():
        say("ASR 已在运行（%d）" % ASR_PORT)
        return True
    if not has_systemctl() or not systemd_running():
        say("跳过 ASR：systemd 不可用（容器里需要手动起 %d，不影响 TTS）" % ASR_PORT)
        return False
    if not has_asr_unit():
        say("跳过 ASR：没有注册 %s 服务（可选组件，不影响 TTS 生成）" % ASR_UNIT)
        return False
    say("启动 ASR（%d）…" % ASR_PORT)
    sh("systemctl start %s 2>&1 || true" % ASR_UNIT, timeout=120)
    t0 = time.time()
    while time.time() - t0 < 120:
        if asr_active():
            say("ASR 就绪（%d），转写与内容校验可用" % ASR_PORT)
            return True
        time.sleep(2)
    say("ASR 未就绪：看 journalctl -u %s（不影响 TTS 生成）" % ASR_UNIT)
    return False


def stop_asr():
    if not asr_active():
        return
    sh("systemctl stop %s 2>/dev/null || true" % ASR_UNIT, timeout=120)
    say("已停 ASR（%d）" % ASR_PORT)


def stop_engine(name):
    if name in ("official", "all"):
        sh("systemctl stop ming-official 2>/dev/null || true")
        sh('pkill -f "[s]erve_official.py" || true')
        say("已停 8010")
    if name in ("fast", "all"):
        sh("systemctl stop ming-tts 2>/dev/null || true")
        say("已停 8000")


def start_engine(name):
    """启动指定引擎；显存互斥，先停另一套。

    启动前先做“能不能启”的检查：缺脚本、缺 systemctl 就直接报错返回，
    否则会白等 wait_health 十几分钟，页面上看起来像卡死。
    """
    if name not in ("official", "fast"):
        return {"ok": False, "error": "未知引擎 %r" % name}

    # 先检查前置条件，再动另一套引擎（避免检查失败却已经把在跑的引擎停了）
    if name == "official" and not has_systemctl():
        return {"ok": False, "error": "找不到 systemctl：完整模式依赖 systemd 服务，"
                                       "请先注册 ming-official.service 或手动启动 8010"}
    if name == "official" and not systemd_running():
        return {"ok": False, "error": "systemd 没有在运行（常见于容器/沙箱）："
                                       "完整模式依赖 systemd，容器里请直接手动起 8010，"
                                       "或在宿主机上跑"}
    if name == "official" and not has_official_unit():
        return {"ok": False, "error": "systemctl 里没有 ming-official 这个服务："
                                       "请把 服务端/ming-official.service 部署到 /etc/systemd/system/ "
                                       "并 systemctl daemon-reload"}
    if name == "fast" and not has_start_script():
        return {"ok": False, "error": "找不到 %s/start_ming.sh：请把服务端脚本部署到该目录，"
                                       "或在 config.cmd / config.sh 里改 MING_SERVICE_ROOT"
                                       % SERVICE_ROOT}

    wsl_guard()
    if name == "official":
        c_free = free_c_gb()
        say("磁盘可用 %.1fGB" % c_free)
        if 0 < c_free < 2:
            return {"ok": False, "error": "磁盘可用空间低于 2GB，已中止（保护磁盘）"}
        stop_engine("fast")
        say("启动 8010（systemd: ming-official）…")
        sh("systemctl restart ming-official 2>&1 || true")
        # 刚停完 8000 时，8010 要 4~5 分钟才能就绪（显存回收慢），240s 会误报失败
        ok = wait_health("http://127.0.0.1:8010/health", 600)
        say("8010 %s" % ("就绪" if ok else "未就绪（冷启动最长约 5 分钟，看 serve 日志）"))
        return {"ok": ok, "engine": "official"}

    if name == "fast":
        c_free = free_c_gb()
        say("磁盘可用 %.1fGB" % c_free)
        stop_engine("official")
        time.sleep(2)
        say("启动 8000 …")
        # 快速模式冷启动 10~15 分钟（含 CUDA graph），脚本自身会等 15 分钟，
        # 所以这里的超时必须比脚本长，否则会误报 TIMEOUT
        out = sh("bash %s/start_ming.sh" % SERVICE_ROOT, timeout=960)
        if out:
            say(out.strip()[-300:])
        ok = wait_health("http://127.0.0.1:8000/health", 1500)
        say("8000 %s" % ("就绪" if ok else "未就绪（冷启动最长约 15 分钟，看 serve 日志）"))
        return {"ok": ok, "engine": "fast"}

    return {"ok": False, "error": "未知引擎 %r" % name}


def status():
    return {
        "official": port_alive(8010),
        "fast": port_alive(8000),
        "asr": asr_active(),
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


def _on_exit():
    """管家退出时把 ASR 一起带走（强杀走不到这里，由「停止.cmd」兜底）。"""
    try:
        stop_asr()
    except Exception:
        pass


if __name__ == "__main__":
    atexit.register(_on_exit)

    def _sig(signum, frame):
        raise SystemExit(0)

    for _s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_s, _sig)
        except Exception:
            pass

    say("服务管家已启动，端口 %d（页面同端口）" % PORT)
    # ASR 在后台线程里拉起来，页面不等它暖机
    threading.Thread(target=start_asr, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--open" in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d/%s" % (PORT, PAGE))).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
