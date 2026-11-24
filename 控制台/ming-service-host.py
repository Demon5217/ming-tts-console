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
import re
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
# 最近一次 ASR 启动失败的原因（给页面 hover 与 /status 用；成功即清空）
_asr_error = ""
# 巡检间隔（秒）；WSL 空闲会把 distro 里的服务清掉，所以要定期补拉
ASR_WATCH_SEC = int(os.environ.get("MING_ASR_WATCH_SEC") or 60)
# 心跳间隔（秒）：WSL 在最后一个会话结束后十几秒就清进程，所以心跳要压到 10 秒以内
ASR_KEEPALIVE_SEC = int(os.environ.get("MING_ASR_KEEPALIVE_SEC") or 10)
# 管家退出中：巡检线程看到它就不再拉起 ASR，避免和 atexit 的 stop 打架
_shutting_down = False
# 拉起 ASR 的互斥锁：启动线程与巡检线程别同时 systemctl start
_asr_start_lock = threading.Lock()
# 用户从状态灯点了"暂停"：巡检不再补拉，直到再点一次或重启管家
_asr_paused = False
# 常驻 WSL 会话的子进程（让 distro 不被清理）
_keepalive = None
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


def asr_last_error(lines=6, limit=300):
    """失败时抓 journal 末几行，去掉时间戳/进程前缀，压成一行给页面展示。

    只读 journal，不触发联网，也不影响 TTS。
    先滤掉 systemd 的 Started/Stopped 之类运营噪音，留下真正能定位问题的行。
    """
    out = sh("journalctl -u %s -n %d --no-pager 2>/dev/null | tail -n %d"
             % (ASR_UNIT, lines * 4, lines * 2), timeout=30) or ""
    rows = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        # "Nov 24 00:02:21 Demon python[315]: xxx" -> "xxx"
        if "]: " in line:
            line = line.split("]: ", 1)[1]
        else:
            line = re.sub(r"^\w{3}\s+\d+\s+\d\d:\d\d:\d\d\s+\S+\s+", "", line)
        if re.match(r"^(Started|Stopped|Stopping|Deactivated|Scheduled restart|"
                    r"Consumed|Ming ASR daemon)\b", line):
            continue
        rows.append(line)
    if not rows:
        return "journal 里没有输出（服务可能没起来）"
    text = " | ".join(rows[-lines:])
    if len(text) > limit:
        # 从尾部按分隔符倒推，避免把开头一条切一半
        chunks = text.split(" | ")
        out_parts = []
        for chunk in reversed(chunks):
            if out_parts and len(" | ".join([chunk] + out_parts)) > limit:
                break
            out_parts.insert(0, chunk)
        text = ("…" if len(out_parts) < len(chunks) else "") + " | ".join(out_parts)
    return text[-limit:] if len(text) > limit else text


def start_asr():
    """管家起来时顺带拉起 ASR；起不来只记日志，绝不影响引擎与页面。

    放在后台线程里做（模型暖机要十几秒），避免拖慢页面打开。
    启动与巡检线程共用一把锁，避免两边同时 systemctl start 打出重复日志。
    """
    global _asr_error
    if not _asr_start_lock.acquire(blocking=False):
        # 另一处正在拉，等它的结果就行
        return asr_active()
    try:
        if asr_active():
            _asr_error = ""
            say("ASR 已在运行（%d）" % ASR_PORT)
            return True
        if not has_systemctl() or not systemd_running():
            _asr_error = "systemd 不可用"
            say("跳过 ASR：systemd 不可用（容器里需要手动起 %d，不影响 TTS）" % ASR_PORT)
            return False
        if not has_asr_unit():
            _asr_error = "没有注册 %s 服务" % ASR_UNIT
            say("跳过 ASR：没有注册 %s 服务（可选组件，不影响 TTS 生成）" % ASR_UNIT)
            return False
        say("启动 ASR（%d）…" % ASR_PORT)
        sh("systemctl start %s 2>&1 || true" % ASR_UNIT, timeout=120)
        t0 = time.time()
        while time.time() - t0 < 120:
            if asr_active():
                _asr_error = ""
                say("ASR 就绪（%d），转写与内容校验可用" % ASR_PORT)
                return True
            time.sleep(2)
        _asr_error = asr_last_error()
        say("ASR 未就绪（%d）：%s（不影响 TTS 生成；排查：journalctl -u %s）"
            % (ASR_PORT, _asr_error, ASR_UNIT))
        return False
    finally:
        _asr_start_lock.release()


def stop_asr():
    if not asr_active():
        return
    sh("systemctl stop %s 2>/dev/null || true" % ASR_UNIT, timeout=120)
    say("已停 ASR（%d）" % ASR_PORT)


def keep_wsl_alive():
    """持有一个常驻 WSL 会话（`sleep infinity`），让 distro 不被空闲清理。

    WSL 在最后一个 wsl.exe 会话结束后十几秒，会把 distro 里的进程（含 systemd 服务）
    一起清掉；ASR 是 disabled 的，清了就不会自己回来。用一条常驻会话顶住，
    比每隔几秒发一次 `wsl true` 便宜得多，也彻底（后者赶不上清理窗口）。
    """
    global _keepalive
    if not IS_WIN:
        return
    if _keepalive is not None and _keepalive.poll() is None:
        return
    try:
        _keepalive = subprocess.Popen(
            ["wsl.exe", "-d", DISTRO, "-e", "/bin/sleep", "infinity"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        say("已持有 WSL 常驻会话（避免 distro 被空闲清理）")
    except Exception as e:
        _keepalive = None
        say("持有 WSL 常驻会话失败：%s" % e)


def release_wsl_alive():
    """管家退出时放掉常驻会话，让 WSL 能照常空闲休眠。"""
    global _keepalive
    if _keepalive is None:
        return
    try:
        if _keepalive.poll() is None:
            _keepalive.kill()
    except Exception:
        pass
    _keepalive = None


def asr_pause():
    """页脚状态灯点一下：停掉 ASR 并让巡检别再补拉（管家仍在，随时可恢复）。"""
    global _asr_paused
    _asr_paused = True
    sh("systemctl stop %s 2>/dev/null || true" % ASR_UNIT, timeout=120)
    say("ASR 已暂停（再点状态灯恢复；管家仍持有 WSL 常驻会话）")
    return True


def asr_resume():
    """恢复 ASR：清掉暂停标记，交给后台线程去拉起（暖机要十几秒）。"""
    global _asr_paused
    _asr_paused = False
    say("恢复 ASR（%d）…" % ASR_PORT)
    threading.Thread(target=start_asr, daemon=True).start()
    return True


def asr_watch():
    """后台线程：顶住 WSL 不被清理 + ASR 掉了就补拉。

    为什么需要这一段：WSL 在最后一个 `wsl.exe` 会话结束后十几秒，就会把 distro 里的进程
    （含 systemd 管理的服务）一起清掉。ASR 是 disabled 的，清了不会自己回来，
    于是页面上灯变红、ASR 复核和音色库转写失效。这里持有一条常驻会话顶住 distro，
    再按 ASR_WATCH_SEC 探端口，真掉了才补拉。Linux 下没有这个问题。
    """
    global _asr_error
    if (os.environ.get("MING_ASR_WATCH") or "").strip().lower() in ("0", "off", "no", "false"):
        say("ASR 巡检已按 MING_ASR_WATCH 关闭")
        return
    try:
        if not has_asr_unit():
            say("未部署 %s，ASR 巡检不启用（可选组件）" % ASR_UNIT)
            return
    except Exception:
        return
    tick = 0
    fails = 0
    while not _shutting_down:
        try:
            # 常驻会话掉了（被 wsl --shutdown/--terminate 之类打断）就重新持有
            keep_wsl_alive()
            tick += 1
            # 只在周期点检查，避免和启动线程在刚起来时撞车；暂停时不补拉
            if tick % max(1, ASR_WATCH_SEC // ASR_KEEPALIVE_SEC) == 0 and not _asr_paused and not asr_active():
                if fails >= 3:
                    fails = 0
                    time.sleep(ASR_WATCH_SEC * 4)
                    continue
                say("巡检发现 ASR 不在（%d），重新拉起…" % ASR_PORT)
                if start_asr():
                    fails = 0
                else:
                    fails += 1
        except Exception as e:
            fails += 1
            say("ASR 巡检异常：%s" % e)
        time.sleep(ASR_KEEPALIVE_SEC)


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
        "asr_error": _asr_error,
        "asr_paused": _asr_paused,
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
        elif u.path == "/asr":
            on = (q.get("on") or [""])[0].lower()
            paused = on in ("0", "false", "off", "pause")
            try:
                if paused:
                    asr_pause()
                else:
                    asr_resume()
                self._json({"ok": True, "paused": paused, "asr": asr_active()})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        pass


def _on_exit():
    """管家退出时把 ASR 一起带走（强杀走不到这里，由「停止.cmd」兜底）。"""
    global _shutting_down
    _shutting_down = True
    try:
        stop_asr()
    except Exception:
        pass
    release_wsl_alive()


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
    # ASR 在后台线程里拉起来，页面不等它暖机；再由巡检线程兜住 WSL 空闲后的掉线
    threading.Thread(target=start_asr, daemon=True).start()
    threading.Thread(target=asr_watch, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if "--open" in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d/%s" % (PORT, PAGE))).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
