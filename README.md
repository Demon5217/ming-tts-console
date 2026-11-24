# Ming TTS Console

给 [Ming-3.0-TTS](https://github.com/Alexwell-Chen/Ming-3.0-TTS) 做的一套本地单页控制台：**一个页面 + 一个管家进程**，把「完整模式（官方推理）」和「快速模式（SGLang）」两套引擎的启停、任务选择、音色管理、批量生成、结果试听全部收在一屏里。

![控制台截图](docs/console.png)

**适合谁**：已经按官方说明装好 Ming-3.0-TTS、想有个界面天天用的本地玩家。Windows + WSL2（或 Linux 原生）+ 单张消费级显卡，开发机是 RTX 3060 Laptop 6GB。

**不适合谁**：想看官方推理代码（去上游仓库）；macOS（没有 CUDA，未适配）；没有模型权重只想看看界面的（页面能开，但生成不了）。

> **本仓库不含模型权重，也不含任何语音素材。** 模型与内置音色文案的版权、许可与合规使用请以 [Ming-3.0-TTS](https://github.com/Alexwell-Chen/Ming-3.0-TTS) 官方说明为准，详见 [THIRD-PARTY.md](THIRD-PARTY.md)。

## 它解决什么问题

官方仓库给的是推理脚本和 Gradio demo，实际用起来会遇到这些琐事：

- 两套推理栈端口不同（8010 / 8000）、显存互斥，手动 `systemctl` 切换很容易忘；
- 参考音频有 `spk` / `prompt` / `prompt_emb` 三种用法，音色像不像、开头稳不稳完全不一样；
- 短句、长文、情绪、方言、自由指令、音色设计、内置角色（IP）是 7 种不同任务，各自吃的参数不同；
- 生成失败经常不是报错，而是「全是噪音」或「开头丢字」，需要自动重试 + ASR 复核。

这套控制台把这些都做成了页面上的开关和默认策略。

## 功能

- **单端口**：管家监听 `8766`，同时托管页面；双击启动脚本即自动开浏览器，引擎启停全在页面上。
- **两套引擎互斥启停**：完整模式（`8010`，约 3.1GB 显存）/ 快速模式（`8000`，约 5.9GB）；启动一套会自动停另一套。
- **WSL 自检**：每次启动引擎前先探测 WSL，卡死则 `wsl --shutdown` 重置并拉起 keepalive。
- **7 种任务**：TTS / BASIC（语速音高音量）/ EMOTION / DIALECT（粤语、川渝）/ FREEFORM / VOICE_DESIGN / IP（内置音色）。
- **音色三路径**：`spk` 纯净音色嵌入（不读逐字稿、开头稳）、`prompt_emb` 官方零样本克隆（最像）、`prompt` 仅参考音频提示（偶发噪音）。
- **任务能力收敛**：切任务后只显示该任务真正生效的参数，无关项直接从界面消失，请求体也只发有效字段。
- **质量控制**：失败自动换种子重试、时长/音量异常检测、ASR 念字复核（防「全是噪音」）、长文按 24 字分段并复用首段音色。
- **音色库**：参考音频 + 逐字稿存在浏览器 IndexedDB，可保存、试听、删除；自动转写走本机 ASR（`8011`）。
- **ASR 随管家启停**：管家起来就在后台拉起 `ming-asr`（`faster-whisper small`，跑 CPU 不占显存），管家退出时一起停；页脚有状态灯，没注册该服务时显示「ASR 未就绪」但不影响 TTS。起不来时管家日志会带出 `journalctl -u ming-asr` 末几行的具体原因，状态灯和文案 hover 也能看到。
- **ASR 掉线自动补拉**：WSL 在最后一个 `wsl.exe` 会话结束后十几秒，就会把 distro 里的进程（含 systemd 服务）一起清掉，所以只靠启动时拉一次不够。管家会持有一条常驻 `wsl -e /bin/sleep infinity` 会话顶住 distro，并每 `MING_ASR_WATCH_SEC`（默认 60 秒）探一次 `8011`，掉了就补拉；退出时（含 `停止.cmd`）放掉这条会话，让 WSL 照常空闲休眠（`MING_ASR_WATCH=0` 可关整套维护）。
- **ASR 暂停/恢复**：页脚 `ASR 就绪` 那个状态灯可以点——点一下停掉 ASR 并让巡检不再补拉（腾 CPU/内存），再点一下恢复暖机（约十秒）。重启管家会回到默认的"随管家自动运行"。
- **内置音色**：233 条内置角色按「角色 → 情绪」聚合成 85 个，用性别 / 年龄 / 语言 / 情绪标签筛选，不用记关键词。
- **其他**：批量长文（一行一个文件）、A/B 对比、请求体查看、生成历史、日志浮层、`Ctrl+Enter` 生成 / `Esc` 停止。

## 架构

```
浏览器（ming-console.html，单文件）
   │  HTTP（同源，127.0.0.1:8766）
   ▼
ming-service-host.py  ── 静态页面 + /start /stop /status /asr ──► wsl.exe (Ubuntu-22.04)
   │                                                          │
   │                                                          ├─ systemd: ming-official → 8010 官方推理
   │                                                          └─ /opt/sglang/start_ming.sh → 8000 SGLang
   │
   └─ /health 轮询、nvidia-smi 显存、磁盘余量
```

## 目录

```
启动.cmd                  日常唯一入口：起管家（页面同端口）+ 自动开浏览器
停止.cmd                  停两套引擎 + 结束管家进程（引擎显存一并释放）
控制台/                   页面 + 管家 + 启动脚本
  config.cmd              唯一的本地配置点（Python 路径、WSL 发行版、端口、SGLang 目录）
  config.sh               Linux 配置点（Python、引擎目录、端口）
  ming-console.html       单文件控制台（内联 CSS/JS）
  ming-service-host.py    服务管家：静态页面 + 引擎启停 API
  wsl-guard.ps1           WSL 自检 / 重置 / keepalive（仅 Windows）
  启动Ming控制台.cmd       被根目录启动.cmd 调用，只起管家
  start.sh / stop.sh      Linux 入口：原生起停，不需要 WSL
服务端/                   跑在 WSL 里的推理服务
  serve_official.py       FastAPI，8010，任务契约 / 分段 / 重试 / ASR 复核
  ming-official.service   systemd unit
  启动脚本/                start_ming.sh、ming-tts.service、ming-asr.service 等
  voice_data.js           内置音色清单（页面数据来源，来源见 THIRD-PARTY.md）
scripts/check.sh          自检：语法 + 个人路径扫描（CI 同款）
.github/workflows/check.yml  push 时跑自检
.github/ISSUE_TEMPLATE/  问题反馈 / 功能建议模板
CONTRIBUTING.md           提 issue 要带的信息、PR 约定、排查顺序
THIRD-PARTY.md            第三方组件、模型与音色资源的许可说明
```

## 环境要求

- Windows 11 + WSL2（任意发行版，默认按 `Ubuntu-22.04` 写，可改）；或 Linux 原生（不用 WSL）
- Python 3.8+（用来跑管家；Windows 默认按 `C:\Program Files\Python313\python.exe` 写，Linux 默认 `python3`，都可改）
- NVIDIA 驱动可用 `nvidia-smi`；6GB 显存起步（两套引擎不能同时常驻）
- 已按 Ming-3.0-TTS 官方说明装好模型与依赖（**本仓库不含模型权重**）
- ASR 可选：把 `服务端/启动脚本/ming-asr.service` 部署到 `/etc/systemd/system/`（`faster-whisper` 装在 `/opt/asr-venv`，跑 CPU）。注册了它，管家启动时会自动拉起、退出时自动停，掉线还会自动补拉；没注册也能用，只是少了逐字稿自动转写和内容校验。
- **macOS 不在支持范围**：没有 CUDA，显存假设不成立，脚本也没做适配

## 快速开始

```bash
git clone https://github.com/Demon5217/ming-tts-console.git
cd ming-tts-console
```

### Windows（WSL2）

1. **只改一个文件**：打开 `控制台/config.cmd`，改这两项（其余脚本都会 `call` 它）：
   ```bat
   set "PYTHON_EXE=C:\Program Files\Python313\python.exe"
   set "MING_WSL_DISTRO=Ubuntu-22.04"
   ```
   - `PYTHON_EXE`：你的 Windows Python 路径（`where python` 可查）
   - `MING_WSL_DISTRO`：`wsl -l -v` 里看到的发行版名，管家和 `wsl-guard.ps1` 都读这个值
   - 同文件还有 `MING_HOST_PORT`（默认 `8766`）和 `MING_SGLANG_DIR`（默认 `/opt/sglang`），一般不用动
   - `MING_ASR_WATCH`（默认开）：部署了 `ming-asr` 后，管家持有一条常驻 WSL 会话防止 distro 被清理，并每 `MING_ASR_WATCH_SEC`（默认 60）秒探一次 `8011`，掉了就补拉；`set MING_ASR_WATCH=0` 可关掉整套维护（之后 ASR 只在管家启动时拉一次）
   > 直接用环境变量也行：先 `set MING_WSL_DISTRO=你的发行版` 再启动，脚本不会覆盖已设置的值。
2. 把 `服务端/` 里的 `serve_official.py` 和 systemd unit 部署进 WSL（示例：`/opt/ming-official/`），模型目录、参考音频目录按官方结构放。
3. 双击**根目录的 `启动.cmd`**，浏览器会打开 `http://127.0.0.1:8766/ming-console.html`。
4. 在页面上点「完整模式」→ 确认冷启动 → 等状态变「就绪」；之后选任务、选音色、贴稿、生成。
5. 收工：双击**根目录的 `停止.cmd`**——停两套引擎并结束管家进程，显存和端口都释放；WSL 发行版会保留，下次启动更快。只想停引擎、继续用页面时，用页面上的停止按钮即可。

### Linux（原生，不需要 WSL）

1. 把 `服务端/` 里的 `serve_official.py`、systemd unit、`启动脚本/start_ming.sh` 部署到 `/opt/sglang`（或任意目录）。
2. 改 `控制台/config.sh`：
   ```bash
   PYTHON_BIN=python3            # 跑管家的解释器
   MING_SERVICE_ROOT=/opt/sglang # start_ming.sh 所在目录
   MING_HOST_PORT=8766           # 管家 + 页面端口
   ```
3. 拉起：
   ```bash
   chmod +x 控制台/start.sh 控制台/stop.sh
   控制台/start.sh             # 只起管家+页面，引擎在页面上点按钮
   控制台/start.sh fast        # 顺便拉起快速模式（8000）
   控制台/start.sh official    # 顺便拉起完整模式（8010）
   控制台/stop.sh              # 停管家+页面；stop.sh --all 连引擎一起停
   ```
4. 浏览器打开 `http://127.0.0.1:8766/ming-console.html`（无桌面环境就用 SSH 端口转发）。

> Linux 下没有 WSL 这一层，管家不会跑 `wsl-guard.ps1`；引擎启停走本地 `systemctl` / `bash start_ming.sh`，所以 `systemctl restart ming-official` 需要相应权限（通常用 sudo 或给服务配好权限）。

### 服务端目录与日志（Windows / Linux 通用）

服务端脚本里的目录都读环境变量，带默认值；`*_service.sh`、`start_ming.sh`、`serve_official.py` 一致：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `MING_APP_DIR` | `/opt/ming-official` | 模型代码目录（`serve_official.py` 的工作目录、`PYTHONPATH` 根） |
| `MING_MODEL_DIR` | `/opt/models/Ming-3.0-tts-0.5B` | 模型权重目录 |
| `MING_SGLANG_DIR` | `/opt/sglang` | SGLang 虚拟环境与 `start_ming.sh` 所在目录 |
| `MING_VENV_PY_VER` | `python3.12` | 虚拟环境里的 Python 版本目录名（拼 `CUDA_HOME` 用） |
| `MING_LOG_DIR` | `/var/log/ming-tts` | 服务日志目录（`start_ming.sh` 失败时 tail 的位置） |
| `MING_FAST_PORT` | `8000` | 快速模式端口 |

`服务端/ming-official.service`、`服务端/启动脚本/ming-tts.service` 里也有同名 `Environment=` 行，部署时按你的路径改这两行即可（日志默认写到 `/var/log/ming-tts/`，注意目录存在且有写权限）。

### 自检

```bash
bash scripts/check.sh    # Python/Shell 语法 + 个人路径扫描，CI 也跑同一套
```

### 反馈与贡献

提 issue 请用仓库里的模板，并带上模式、显卡显存、系统、任务类型、音色路径和日志——这几项缺一个都很难定位。完整要求和 PR 约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 端口

`8766`（管家 + 页面，可用 `MING_HOST_PORT` 改）、`8010`（完整模式）、`8000`（快速模式）、`8011`（可选 ASR）。改 8766 之外还要同步页面里的接口地址。

## 管家 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/`、`/ming-console.html` | 控制台页面（同目录，`no-store`） |
| GET | `/status` | `{official, fast, asr, asr_error, asr_paused, vram:{used,free}, c_free_gb, log}` |
| GET | `/health` | 管家自身健康检查 |
| POST | `/start?engine=official\|fast` | 先 WSL 自检，再启引擎并轮询 `/health`（完整模式最长 10 分钟、快速模式最长 25 分钟） |
| POST | `/stop?engine=official\|fast\|all` | 停引擎 |
| POST | `/asr?on=1\|0` | 恢复 / 暂停 ASR（页脚状态灯点一下就是调它） |

`官方 /start` 前会检查磁盘余量（低于 2GB 直接中止）；两个引擎启动前都会先检查前置条件——快速模式要求 `MING_SERVICE_ROOT/start_ming.sh` 存在，完整模式要求 `systemctl` 可用、systemd 真在运行（容器里通常是 `offline`）、且 `systemctl cat ming-official` 能查到 unit——任一不满足立即返回错误，不会白等到超时。

`start.sh` 会把实际用的端口写进 `.ming-host.port`，`stop.sh` 优先读这个记录，所以临时用 `MING_HOST_PORT=xxxx ./start.sh` 启动后，直接 `./stop.sh` 也能停对端口；pid 不存在（手工启动）或已过期时会退化为按端口找监听进程（`fuser` → `lsof` → `ss`），避免留下孤儿进程。已在运行时再执行 `start.sh` 会复用原实例，并以记录里的端口为准。

## 已知限制

- 6GB 显存只够跑一套，两套引擎互斥；切换要重新冷启动（完整模式约 20–60 秒，快速模式 10–15 分钟含 CUDA graph）。
- `fp16` 在这套权重上跑不通（bf16/Half dtype 冲突），只能用默认精度。
- `flow_steps` 外部不可调（官方 `generate()` 不接该参数）；`cfg` 降档反而更慢（实测 1.0→15.6s、1.5→12.2s、2.0→10.7s）。
- 内置音色的性别 / 年龄是从标签文案推断的（`男性（男童）`、`老年` 这类字样），不是模型独立字段。
- 启动脚本分两套：Windows 用 `.cmd` + PowerShell（含 WSL 自检），Linux 用 `start.sh` / `stop.sh`；macOS 未适配（无 CUDA）。
- Linux 下引擎启停依赖 `systemctl`，服务未注册成 systemd unit 时需要手动跑 `start_ming.sh`。
- `wsl.exe` 的 `-d` 参数**不能加引号**（写成 `-d "%VAR%"` 会把引号当成发行版名的一部分，报 `WSL_E_DISTRO_NOT_FOUND`），仓库里的脚本已按 `-d %VAR%` 写法规避。自己写脚本时注意这条。

## 许可

代码部分 MIT，见 [LICENSE](LICENSE)。

模型权重、内置音色与参考音频**不在本仓库内**，其许可与使用条款以 Ming-3.0-TTS 官方仓库和模型发布方为准。生成内容的版权与平台合规由使用者自行负责。
