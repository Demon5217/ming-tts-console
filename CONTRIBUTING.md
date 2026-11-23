# 贡献指南

感谢你愿意花时间改进这套控制台。这个项目比较特殊：**它是给本地单机环境用的一层壳，大部分问题都跟你的显卡、WSL、模型目录有关**，所以提 issue 和提 PR 之前，请先看完下面两节。

## 提 issue 前

### 1. 先跑自检

```bash
bash scripts/check.sh
```

它会做 Python / Shell 语法检查和「个人路径」扫描（和你机器上的 CI 是同一套）。语法错误、脚本里残留本机路径这类问题，它会直接告诉你。

### 2. 用 bug 模板，把这几样带上

没有这些信息基本没法定位——「生成出来全是噪音」在 3060 笔记本和 4090 台式上是两回事：

- **模式**：完整模式（`8010`）/ 快速模式（`8000`）/ 两者都不是
- **显卡与显存**：`nvidia-smi` 里那行显存数字
- **系统**：Windows 11 + WSL2（哪个发行版）还是 Linux 原生
- **任务类型**：TTS / BASIC / EMOTION / DIALECT / FREEFORM / VOICE_DESIGN / IP
- **音色路径**：`spk` / `prompt_emb` / `prompt`（这决定了「像不像」和「开头稳不稳」）
- **日志**：页面右下角日志浮层的内容，或 `MING_LOG_DIR`（默认 `/var/log/ming-tts/`）里的 `serve.log`
- 最好附一段**复现用的文本**（长度、有没有标点、中英混排）

### 3. 别贴敏感内容

**不要**在 issue 里贴 GitHub token、内网地址、自己机器上的用户目录绝对路径（Windows 的盘符路径、WSL 里映射出来的挂载盘路径都不行）、自己或他人的语音素材。日志里如果带这些，先删掉再贴。

## 提 PR

### 能直接提的

- 页面交互、样式、文案（`控制台/ming-console.html` 是单文件，内联 CSS/JS，不引入框架和构建步骤）
- 错误提示更清楚、日志更可读
- 文档修正（README / THIRD-PARTY / 本文件）
- 帮助脚本更健壮（前置检查、端口兜底、异常退出清理）

### 需要先开 issue 讨论的

- 改 `/start` `/stop` `/status` 的**接口契约**（页面和服务端要同步改，别人部署的旧页面会挂）
- 新增任务类型或改请求体字段（要和 `服务端/serve_official.py` 的任务契约对齐）
- 引入第三方依赖、构建工具、包管理器
- 换掉 Windows 入口脚本的形态（`.cmd` + PowerShell 这套是刻意选的：无需运行时）

### 代码约定

- **不提交**：模型权重、语音/参考音频、`产物/`、`服务端/日志/`、`*.log`、`*.bak`、`.ming-host.*`（`.gitignore` 已覆盖，别用 `-f` 绕过）
- **不留本机路径**：新增脚本里的目录一律走环境变量（服务端见 README 的变量表；控制台侧用 `config.cmd` / `config.sh`）
- **不写死用户名/邮箱/机器名**：`scripts/check.sh` 会拦
- HTML 改动请保持「单文件、无外部依赖、不联网」
- 改 `控制台/*.cmd` 请保持 **CRLF + 纯 ASCII 注释**（`chcp 65001` 之前的中文注释在 GBK 控制台下会解析异常，这是踩过的坑）
- 改 `.sh` / `.py` / `.html` / `.md` / `.yml` 保持 **LF**

### 提交前

```bash
bash scripts/check.sh          # 必须通过
# 有 Python 环境时可以顺手：
PYTHON_BIN=python3 bash scripts/check.sh
```

CI（`.github/workflows/check.yml`）会在 push 到 `main` 和每个 PR 上跑同一套脚本，红了就是有上述问题之一。

### commit 信息

中文短句，说清「改了什么 + 为什么」，一行标题足够；范围大就在正文里分点。例如：

```
快速模式启动前置检查：start_ming.sh 缺失时立即报错
修 stop.sh 在 pid 过期后留孤儿进程，改为按端口兜底
```

## 关于「跑不起来」

这套东西的绝大多数「跑不起来」都不是代码问题，而是环境问题。按这个顺序排查：

1. 模型目录对不对（`MING_MODEL_DIR`，里面应该有 `model.safetensors` 之类的权重）
2. 推理环境装没装好（完整模式走 `/opt/ming-official`，快速模式走 `/opt/sglang` 的 venv）
3. 显存够不够（6GB 只够一套，两套互斥）
4. WSL 卡死没有（Windows 侧管家启动前会自检，卡死自动 `wsl --shutdown`；手动可用 `wsl --shutdown` 重置）
5. 端口被占没有（`8766` / `8010` / `8000` / `8011`）

如果卡在第 1、2 步，请先去 [Ming-3.0-TTS](https://github.com/Alexwell-Chen/Ming-3.0-TTS) 上游仓库——**本仓库不含模型权重与推理依赖**，也不负责上游安装问题。

## 许可

提交代码即表示同意以本仓库的 [MIT](LICENSE) 许可发布。涉及第三方素材时请先看 [THIRD-PARTY.md](THIRD-PARTY.md)。
