# -*- coding: utf-8 -*-
"""Ming-3.0-TTS 完整模式本地服务（8010）

双模式：
  mode="clone"  参考音频 + 逐字稿  -> 音色最像，适合普通配音
  mode="style"  只传参考音频做说话人嵌入 -> 支持方言/情绪/语速控制，音色略降

自带兜底：按字数算 token 上限、音量/时长双重校验、不合格换种子重试、长文自动分段。
"""
import os
import sys
import io
import re
import math
import json
import base64
import hashlib
import tempfile
import threading
import time

sys.path.insert(0, "/opt/ming-official")
os.chdir("/opt/ming-official")

import numpy as np
import torch
import soundfile as sf
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ming_tts import MingTTS

MODEL_DIR = "/opt/models/Ming-3.0-tts-0.5B"
TOK_PER_SEC = 12.5        # 实测：输出 token 数 ≈ 时长 × 12.5
CHARS_PER_SEC = 4.5       # 普通话平均 4.5 字/秒
GOOD_RMS_DB = -35.0       # 低于此判为近乎静音
DUR_LOW = 0.80            # 时长下限（相对 字数/4.5 秒）：低于此多半是丢字/截头
DUR_HIGH = 2.20           # 时长上限：超过此多半是拖长/重复
# 只有"严重异常"才触发重试：轻微信速差异（快速 0.72× 之类）不算错
DUR_SEVERE_LOW = 0.55     # <0.55× 期望 → 明显截头/只念了一半
DUR_SEVERE_HIGH = 3.00    # >3× 期望 + 2s → 明显复读/拖尾

app = FastAPI(title="Ming TTS Full Mode")
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_lock = threading.Lock()
_model = None
_ref_cache = {}

# 副语言标签（frontend/toolkit.PARALINGUISTIC_TAGS），大小写敏感
TAG_RE = re.compile(
    r"\[(Breathing|Cough|Sigh|Laughter|Surprise-ah|Surprise-oh|"
    r"Dissatisfaction-hnn|Uhm|Confirmation-en|Question-ei|Question-ah|"
    r"Question-en|Question-oh|Question-huh)\]"
)

# 非中英的目标语言：这类正文和中文逐字稿语种不一致，必须走「仅音色」路径
_ALT_LANGS = {"ja", "japanese", "ko", "korean", "de", "german", "fr", "french", "es", "spanish"}


def alt_lang(language_id):
    return bool(language_id) and str(language_id).strip().lower() in _ALT_LANGS


# 正文里出现假名/谚文时，即使没选目标语言也按对应语言处理，避免被中文规则读坏
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")


def load_model():
    global _model
    if _model is None:
        _model = MingTTS(MODEL_DIR, device="cuda:0", text_normalization=False)
    return _model


def rms_db(wav: torch.Tensor) -> float:
    x = wav.float().flatten()
    return 20 * math.log10(max(float(x.pow(2).mean().sqrt()), 1e-9))


def split_text(text: str, max_len: int = 24, min_len: int = 8):
    text = (text or "").strip()
    if not text:
        return []
    # 副语言标签是模型的原样 token，绝不能从中间切断：先换成一个单字符占位符，
    # 让下面的按标点切分 / 长度截断都把它当成一个不可分单元，切完再还原。
    tags = []
    def _protect(m):
        tags.append(m.group(0))
        return "\ue000"
    protected = TAG_RE.sub(_protect, text)
    out = _split_plain(protected, max_len, min_len)
    if not tags:
        return out
    restored = []
    for part in out:
        while "\ue000" in part:
            if not tags:
                break
            part = part.replace("\ue000", tags.pop(0), 1)
        restored.append(part)
    return restored


def _split_plain(text: str, max_len: int, min_len: int):
    parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;])", text) if p.strip()]
    out = []
    for p in parts:
        if len(p) <= max_len:
            out.append(p)
            continue
        buf = ""
        for s in re.split(r"(?<=[，,、：:])", p):
            if not s:
                continue
            if len(buf) + len(s) <= max_len:
                buf += s
            else:
                if buf:
                    out.append(buf)
                buf = s
        if buf:
            out.append(buf)
    merged = []
    for p in out:
        # 只在合并后仍不超过硬上限时才并，避免拼回长段触发退化
        if merged and (len(p) < min_len or len(merged[-1]) < min_len) \
                and len(merged[-1]) + len(p) <= max_len:
            merged[-1] += p
        else:
            merged.append(p)
    return merged


def write_ref(ref_audio: str) -> str:
    """把前端传来的 data URI / base64 / 本地路径统一落成一个临时 wav。"""
    if not ref_audio:
        raise ValueError("缺少参考音频")
    if ref_audio.startswith("/") and os.path.exists(ref_audio):
        return ref_audio
    if ref_audio.startswith("data:"):
        payload = ref_audio.split(",", 1)[1]
    else:
        payload = ref_audio
    raw = base64.b64decode(payload)
    key = hashlib.md5(raw).hexdigest()
    path = _ref_cache.get(key)
    if path and os.path.exists(path):
        return path
    tmp = os.path.join(tempfile.gettempdir(), "ming_ref_%s.wav" % key)
    with open(tmp, "wb") as f:
        f.write(raw)
    _ref_cache[key] = tmp
    return tmp


def _wav_to_16k(path):
    """参考音频 -> 16k 单声道 tensor，给说话人编码器用。"""
    import torchaudio
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    wav = torch.from_numpy(data.T.copy() if getattr(data, "ndim", 1) > 1 else data.copy()).float()
    if wav.ndim == 1:
        wav = wav.unsqueeze(0)
    if sr != 16000:
        wav = torchaudio.transforms.Resample(sr, 16000)(wav)
    return wav


def _tensor_to_16k(wav):
    """生成结果（44.1k）-> 16k 单声道 tensor。"""
    import torchaudio
    w = wav.detach().float().cpu()
    if w.ndim == 1:
        w = w.unsqueeze(0)
    if 44100 != 16000:
        w = torchaudio.transforms.Resample(44100, 16000)(w)
    return w


_REF_EMB_CACHE = {}
_ASR_CACHE = {}


def ref_embedding(model, ref_path=None, spk_pt=None):
    """参考音色的说话人嵌入，用来给生成结果打「像不像」的分。"""
    try:
        if spk_pt:
            key = "pt:" + spk_pt
            if key in _REF_EMB_CACHE:
                return _REF_EMB_CACHE[key]
            obj = torch.load(spk_pt, map_location="cpu")
            if isinstance(obj, dict):
                for k in ("spk_emb", "speaker_embedding", "embedding", "emb"):
                    if k in obj:
                        obj = obj[k]
                        break
            emb = obj if torch.is_tensor(obj) else torch.tensor(obj)
            if emb.dim() == 1:
                emb = emb.unsqueeze(0)
            _REF_EMB_CACHE[key] = emb.float()
            return _REF_EMB_CACHE[key]
        if ref_path:
            key = "wav:" + ref_path
            if key in _REF_EMB_CACHE:
                return _REF_EMB_CACHE[key]
            emb = model.spkemb_extractor(_wav_to_16k(ref_path))
            if isinstance(emb, list):
                emb = emb[0]
            if not torch.is_tensor(emb):
                return None
            if emb.dim() == 1:
                emb = emb.unsqueeze(0)
            _REF_EMB_CACHE[key] = emb.float()
            return _REF_EMB_CACHE[key]
    except Exception:
        return None
    return None


def speaker_sim(model, ref_emb, wav):
    """生成音频与参考音色的余弦相似度（0~1，越大越像）。"""
    if ref_emb is None:
        return None
    try:
        gen = model.spkemb_extractor(_tensor_to_16k(wav))
        if isinstance(gen, list):
            gen = gen[0]
        if not torch.is_tensor(gen):
            return None
        if gen.dim() == 1:
            gen = gen.unsqueeze(0)
        import torch.nn.functional as F
        return round(float(F.cosine_similarity(ref_emb.float().cpu(), gen.float().cpu(), dim=-1).mean()), 3)
    except Exception:
        return None


def write_spk_pt(b64: str) -> str:
    """前端传来的 .pt（base64）落到本地缓存，返回路径。"""
    raw = base64.b64decode(b64.split(",")[-1])
    key = hashlib.md5(raw).hexdigest()
    path = os.path.join(tempfile.gettempdir(), "ming_spk_%s.pt" % key)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(raw)
    return path


def embed_pt(model, ref_path):
    """参考音频 → 说话人嵌入 .pt（按路径缓存）。只用音色、不吃参考朗读内容。"""
    key = hashlib.md5(("emb:" + ref_path).encode("utf-8")).hexdigest()
    path = os.path.join(tempfile.gettempdir(), "ming_emb_%s.pt" % key)
    if os.path.exists(path):
        return path
    wav = _wav_to_16k(ref_path)
    emb = model.spkemb_extractor(wav)
    if isinstance(emb, list):
        emb = emb[0]
    torch.save({"spk_emb": emb}, path)
    return path


ASR_PASS = 0.55


ASR_URL = "http://127.0.0.1:8011/asr"


def asr_transcribe(path, timeout=180):
    """调用常驻 ASR 服务（模型只加载一次，避免每次 70 秒的冷启动）。"""
    import urllib.request
    data = json.dumps({"path": path}).encode()
    req = urllib.request.Request(ASR_URL, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        obj = json.loads(r.read().decode())
    if not obj.get("ok"):
        raise RuntimeError(obj.get("error") or "asr failed")
    return (obj.get("text") or "").strip()


def asr_verify(wav, text):
    """把生成结果转写回来，和正文比对（3-gram 召回率）。用于抓"内容是噪音/串词"。"""
    path = os.path.join(tempfile.gettempdir(),
                        "ming_verify_%s.wav" % hashlib.md5(str(time.time()).encode()).hexdigest()[:10])
    sf.write(path, wav.float().cpu().numpy().reshape(-1), samplerate=44100,
             format="WAV", subtype="PCM_16")
    try:
        heard = asr_transcribe(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    norm = lambda s: re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")
    a, b = norm(text), norm(heard)
    if not a:
        return heard, 0.0
    grams = [a[i:i + 3] for i in range(max(len(a) - 2, 1))]
    hit = sum(1 for g in grams if g in b)
    return heard, round(hit / len(grams), 3)


def _wav_b64(wav) -> str:
    buf = io.BytesIO()
    sf.write(buf, wav.float().cpu().numpy().reshape(-1), samplerate=44100,
             format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode()


def save_seg_wav(wav, tag: str) -> str:
    """把某一段的生成结果存成 wav，供后续段当参考（音色统一）。"""
    path = os.path.join(tempfile.gettempdir(), "ming_seg_%s.wav" % tag)
    sf.write(path, wav.float().cpu().numpy().reshape(-1), samplerate=44100,
             format="WAV", subtype="PCM_16")
    return path


def plan_cap(chars: int) -> int:
    expected = max(chars / CHARS_PER_SEC, 0.8)
    lim = max(expected * 2.2, expected + 4.0)
    return int(min(max(lim * TOK_PER_SEC, 40), 1400)), expected


def synth_one(model, seg, *, mode, ref_path, ref_text, task, controls, instruction, seed,
              cap_scale=1.0, language_id=None, tuning=None, spk_pt=None,
              instruction_schema=None, raw_text=False, ref_mode=None, max_length=None):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    cap, expected = plan_cap(len(seg))
    cap = max(int(cap * cap_scale), 30)
    kwargs = dict(
        task=task,
        text=seg,
        controls=controls or None,
        instruction=instruction or None,
        max_decode_steps=cap,
    )
    # 生成参数（cfg 引导强度 / sigma 噪声 / temperature 采样温度 / trim_silence 句尾裁剪）
    if tuning:
        for k in ("cfg", "sigma", "temperature", "trim_silence"):
            if tuning.get(k) is not None:
                kwargs[k] = tuning[k]
    if instruction_schema:
        kwargs["instruction_schema"] = instruction_schema
    if raw_text:
        kwargs["raw_text"] = True
    if max_length:
        # 官方 infer.py 的 --max_length：按标点切分的目标字数（长句不硬切）
        kwargs["max_length"] = int(max(8, min(48, max_length)))
    if language_id and task in ("TTS", "MULTILINGUAL", "FREEFORM"):
        # 中文/英文可省略；日韩德法西必须显式指定，模型才会绕过中英文文本归一化
        kwargs["language_id"] = language_id
    if task == "IP":
        # 内置角色音色：只靠 ip 字段，不叠参考音频/说话人嵌入
        pass
    elif task in ("FREEFORM", "VOICE_DESIGN", "STYLE") and ref_path is None:
        # FREEFORM / VOICE_DESIGN 自带描述，官方建议不传参考音频，用零样本说话人嵌入
        kwargs["use_zero_spk_emb"] = True
    elif mode == "clone" and ref_path is not None and not alt_lang(language_id) and \
            (ref_mode or "spk").strip() in ("prompt", "prompt_emb"):
        # 中/英：参考音频 latent + 逐字稿（prompt_emb 再叠说话人嵌入，即官方零样本克隆三件套）
        # 逐字稿必须和参考音频逐字对齐：标点会插入额外 token 破坏对齐（实测加逗号就崩成噪声），
        # 所以这里把标点/空白全部去掉再喂给模型
        kwargs["prompt_wav_path"] = ref_path
        kwargs["prompt_text"] = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", ref_text or "")
        if (ref_mode or "").strip() == "prompt_emb":
            kwargs["use_spk_emb"] = True
    elif spk_pt:
        # 预抽好的说话人嵌入（.pt）：跳过每次重新编码参考音频
        kwargs["spk_emb_path"] = spk_pt
        kwargs["use_spk_emb"] = True
    elif ref_path is None:
        kwargs["use_zero_spk_emb"] = True
    else:
        # 日/韩/德/法/西：正文语种和逐字稿语种不一致时，逐字稿会直接把模型带偏
        # （实测 clone + 中文逐字稿念日语 -> 0.4s / -53dB 的静音噪声）
        kwargs["prompt_wav_path"] = ref_path
        kwargs["use_spk_emb"] = True
    t0 = time.time()
    wav = model.synthesize(**kwargs)
    return wav, cap, expected, time.time() - t0


def judge(wav, expected):
    d = float(wav.shape[-1]) / 44100.0
    level = rms_db(wav)
    dur_ok = (expected * DUR_LOW) <= d <= (expected * DUR_HIGH + 0.5)
    lvl_ok = level >= GOOD_RMS_DB
    score = 0.0
    if lvl_ok:
        score += 1.0
    else:
        score += max(0.0, (level - (-70.0)) / 35.0)
    if dur_ok:
        score += 1.0
    else:
        ratio = d / max(expected, 0.1)
        score += max(0.0, 1.0 - abs(math.log(ratio)) / 1.5)
    return dict(duration=round(d, 2), rms=round(level, 1),
                dur_ok=bool(dur_ok), level_ok=bool(lvl_ok), score=round(score, 2))


TASK_FIELDS = {
    "BASIC": {"speed", "pitch", "volume"},
    "DIALECT": {"dialect"},
    "EMOTION": {"emotion"},
    "VOICE_DESIGN": {"style", "pitch", "speed", "volume"},
    "STYLE": {"style", "pitch", "speed", "volume"},
    "IP": {"ip"},
    "FREEFORM": set(),
    "TTS": set(),
}
TASK_ERR = ""


def normalize_task_inputs(task, controls, instruction):
    """把前端一股脑传的参数收敛成该任务真正接受的字段。

    模型侧规则（tts_tasks.build_task_generation_kwargs）：
    - BASIC / DIALECT / EMOTION / VOICE_DESIGN / STYLE / IP：只能吃各自的 controls，传 instruction 直接报错
    - FREEFORM：只能吃 instruction，传 controls 直接报错
    - TTS：都不吃 controls，instruction 可选
    """
    global TASK_ERR
    task = (task or "TTS").strip().upper()
    controls = dict(controls or {})
    instr = (instruction or "").strip()

    if task in ("VOICE_DESIGN", "STYLE"):
        # 这两类共用 style 字段，其余声音属性由模型内部生成，这里只保留 style 与三围
        keep = {k: v for k, v in controls.items() if k in TASK_FIELDS[task] and v}
        if "style" not in keep:
            TASK_ERR = "VOICE_DESIGN/STYLE 需要 style 描述（控制台的「自由指令」框填风格即可）"
            if instr:
                keep["style"] = instr
            else:
                return None, None, None
        return task, keep, None

    if task in ("BASIC", "DIALECT", "EMOTION", "IP"):
        keep = {k: v for k, v in controls.items() if k in TASK_FIELDS[task] and v}
        if task == "IP" and "ip" not in keep and instr:
            keep["ip"] = instr
        if not keep:
            need = ", ".join(sorted(TASK_FIELDS[task]))
            TASK_ERR = "%s 需要至少一个控制项（%s）" % (task, need)
            return None, None, None
        return task, keep, None

    if task == "FREEFORM":
        if not instr:
            TASK_ERR = "FREEFORM 需要在「自由指令」框里写一句描述"
            return None, None, None
        return task, {}, instr

    if task == "MULTILINGUAL":
        # 多语言与 TTS 同一套运行时，区别只是必须带 language_id（在 tts() 里校验）
        return "MULTILINGUAL", {}, instr or None

    return "TTS", {}, instr or None


class TTSRequest(BaseModel):
    text: str
    mode: str = "clone"
    ref_audio: str = ""
    ref_text: str = ""
    task: str = "TTS"
    controls: dict | None = None
    instruction: str | None = None
    seed: int | None = None
    max_retries: int = 4
    language_id: str = ""
    # 进阶生成参数
    cfg: float | None = None
    sigma: float | None = None
    temperature: float | None = None
    trim_silence: bool | None = None
    # 音色设计/自由指令多段时，用第 1 段当后续段的参考，保证整篇同一个音色
    reuse_ref: bool = True
    # 预抽好的说话人嵌入（base64 的 .pt），有就省掉每次重新编码参考音频
    spk_emb: str = ""
    # 段间静音秒数（0.2 紧凑 / 0.35 默认 / 0.5 旁白感）
    gap: float | None = None
    # 只重生成某一段（1 起），用于逐段返工
    seg_only: int | None = None
    # 结构化指令字段名：zh（默认）或 en
    instruction_schema: str = ""
    # benchmark 整段模式：不切分不归一化
    raw_text: bool = False
    # 单段语义长度：按标点切分的目标字数（越小停顿越多，越大越连贯）
    max_length: int | None = None
    # 音色路径：spk（纯净说话人嵌入，默认）/ prompt（参考音频+逐字稿）/ prompt_emb（官方三件套）
    ref_mode: str = "spk"
    # 用 ASR 复核生成内容，抓"全是噪音/串词"，不合格换种子重试
    verify_text: bool = True


@app.get("/health")
def health():
    used = 0
    if torch.cuda.is_available():
        used = int(torch.cuda.memory_allocated() / 1048576)
    return {"status": "ok", "model_loaded": _model is not None,
            "vram_mb": used, "tasks": ["TTS", "BASIC", "EMOTION", "DIALECT", "FREEFORM", "VOICE_DESIGN", "IP", "MULTILINGUAL"]}


class SpkRequest(BaseModel):
    ref_audio: str


@app.post("/spkemb")
def spkemb(req: SpkRequest):
    """把参考音频压成一个说话人嵌入 .pt（base64 返回），下次直接复用。"""
    try:
        path = write_ref(req.ref_audio)
        with _lock:
            model = load_model()
            wav = _wav_to_16k(path)
            if wav.device.type == "cpu" and torch.cuda.is_available():
                wav = wav
            emb = model.spkemb_extractor(wav)
        buf = io.BytesIO()
        torch.save({"spk_emb": emb if not isinstance(emb, list) else emb[0]}, buf)
        return {"ok": True, "pt": base64.b64encode(buf.getvalue()).decode(),
                "size": len(buf.getvalue())}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


@app.post("/asr")
def asr(req: SpkRequest):
    """参考音频转写：判断用户填的逐字稿是否与音频一致（对不上时 prompt 路径开头会乱）。
    走常驻服务（faster-whisper 装在 /opt/asr-venv，不进主环境）。"""
    try:
        path = write_ref(req.ref_audio)
        key = "asr:" + path
        if key in _ASR_CACHE:
            return {"ok": True, "text": _ASR_CACHE[key], "cached": True}
        text = asr_transcribe(path, timeout=180)
        _ASR_CACHE[key] = text
        return {"ok": True, "text": text, "cached": False}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


@app.post("/tts")
def tts(req: TTSRequest):
    if not req.text.strip():
        return JSONResponse(status_code=400, content={"ok": False, "error": "文本为空"})

    # 按模型的任务契约做参数清洗：结构化任务只吃自己的 controls，且不能传 instruction
    task, controls, instruction = normalize_task_inputs(req.task, req.controls, req.instruction)
    if task is None:
        return JSONResponse(status_code=400, content={"ok": False, "error": TASK_ERR})

    ref_path = None
    if req.ref_audio.strip():
        try:
            ref_path = write_ref(req.ref_audio)
        except Exception as e:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
    elif req.spk_emb.strip():
        # 只给嵌入不给参考音频：音色由 .pt 决定，合法
        pass
    elif task not in ("IP", "FREEFORM", "VOICE_DESIGN"):
        return JSONResponse(status_code=400, content={"ok": False, "error": "缺少参考音频"})

    mode = "clone" if req.mode == "clone" else "style"
    if task != "IP" and mode == "clone" and not req.ref_text.strip():
        return JSONResponse(status_code=400, content={"ok": False, "error": "clone 模式需要参考音频逐字稿"})

    # 目标语言：中文/英文可省略；日、韩、德、法、西必须显式给，且必须走 MULTILINGUAL
    lang = (req.language_id or "").strip()
    if lang.lower() in ("zh", "zh-cn", "chinese", "en", "english", "auto", ""):
        lang = lang if lang.lower() in ("en", "english") else ""
    elif task in ("TTS", "MULTILINGUAL"):
        task = "MULTILINGUAL"
    # 没选语言、但正文里是假名/谚文：自动按对应语言走，避免被中文归一化读坏
    if not lang and task in ("TTS", "MULTILINGUAL"):
        if _KANA_RE.search(req.text):
            lang, task = "ja", "MULTILINGUAL"
        elif _HANGUL_RE.search(req.text):
            lang, task = "ko", "MULTILINGUAL"
    if task == "MULTILINGUAL" and not lang:
        return JSONResponse(status_code=400, content={"ok": False, "error": "MULTILINGUAL 需要 language_id（ja/ko/de/fr/es）"})

    segments = split_text(req.text)
    # 单段重生成：只跑指定段，返回该段音频，供页面替换后本地重新拼接
    only = req.seg_only
    if only is not None:
        if only < 1 or only > len(segments):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "seg_only 超出范围（共 %d 段）" % len(segments)})
        segments = [segments[only - 1]]
    if req.seed is not None:
        base_seed = req.seed
    else:
        # 不写死固定种子：按正文散列，同一句稳定可复现、换句自然换种子，
        # 避免"某一句永远命中截头版本"（seed=1895 就曾把 19 字读成 2.7 秒）
        base_seed = int(hashlib.md5(req.text.encode("utf-8")).hexdigest()[:8], 16) % 100000
    logs = []
    chunks = []
    metas = []
    tuning = {"cfg": req.cfg, "sigma": req.sigma,
              "temperature": req.temperature, "trim_silence": req.trim_silence}
    tuning = {k: v for k, v in tuning.items() if v is not None}
    schema = (req.instruction_schema or "").strip() or None
    if schema and task not in ("VOICE_DESIGN", "STYLE", "BASIC", "EMOTION", "DIALECT", "IP"):
        schema = None
    if req.raw_text:
        logs.append("整段直送（raw_text）：不切分不归一化，只适合单句短文本")
    spk_pt = None
    if req.spk_emb.strip():
        try:
            spk_pt = write_spk_pt(req.spk_emb)
            logs.append("音色：使用缓存的说话人嵌入（跳过参考音频重新编码）")
        except Exception as e:
            logs.append("说话人嵌入解析失败，已忽略：%s" % e)
    # 纯净音色路径（默认）：参考音频只抽说话人嵌入，不把参考朗读当提示喂给模型
    # 实测 prompt_wav+逐字稿 会把参考音频的内容带进开头（首句乱码率 0.75），
    # 纯嵌入路径同句 5 个种子首句对率 1.00、sim 0.73~0.83
    if (mode == "clone" and ref_path and spk_pt is None
            and (req.ref_mode or "spk").strip() not in ("prompt", "prompt_emb")
            and not alt_lang(lang)
            and task not in ("IP", "FREEFORM", "VOICE_DESIGN", "STYLE")):
        try:
            with _lock:
                spk_pt = embed_pt(load_model(), ref_path)
            logs.append("音色：纯净嵌入路径（不用参考朗读当提示，开头更稳）")
        except Exception as e:
            logs.append("说话人嵌入提取失败，回退参考音频路径：%s" % e)
    # 音色设计/自由指令：多段时用第 1 段当后续段的参考，整篇音色统一
    design = task in ("FREEFORM", "VOICE_DESIGN", "STYLE")
    reuse = design and req.reuse_ref and len(segments) > 1
    seg_ref_path, seg_ref_text, seg_spk = ref_path, req.ref_text, spk_pt
    if alt_lang(lang):
        if _KANA_RE.search(req.text) and lang != "ja":
            logs.append("检测到假名，已按日语处理")
        if mode == "clone":
            logs.append("多语言：参考音频逐字稿会被忽略，改走仅音色路径（否则易出噪声）")
        else:
            logs.append("多语言：目标语言 %s" % lang)
    if tuning:
        logs.append("生成参数：" + " / ".join("%s=%s" % (k, v) for k, v in tuning.items()))

    with _lock:
        model = load_model()
        ref_emb = None
        if task != "IP":
            ref_emb = ref_embedding(model, ref_path, spk_pt)
        for idx, seg in enumerate(segments, 1):
            best = None
            # 采样带随机性（VAE 后验采样 + 全局 RNG），同一句偶尔会出"截头"版本，
            # 所以按 时长/电平 判定不合格就换种子重试，最多 6 次
            tries = max(1, min(req.max_retries, 6))
            # 重试只换种子、不再缩 cap：缩 cap 会把"本来就短"的音频压得更短
            scales = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
            for attempt in range(tries):
                seed = base_seed + attempt * 37
                scale = scales[min(attempt, len(scales) - 1)]
                try:
                    wav, cap, expected, cost = synth_one(
                        model, seg, mode=mode, ref_path=seg_ref_path, ref_text=seg_ref_text,
                        task=task, controls=controls, instruction=instruction,
                        seed=seed, cap_scale=scale, language_id=lang,
                        tuning=tuning, spk_pt=seg_spk,
                        instruction_schema=schema, raw_text=req.raw_text, ref_mode=req.ref_mode,
                        max_length=req.max_length)
                except Exception as e:
                    logs.append("段%d 第%d次 异常 %s: %s" % (idx, attempt + 1, type(e).__name__, e))
                    continue
                m = judge(wav, expected)
                m.update(seg=idx, attempt=attempt + 1, seed=seed, cap=cap, cost=round(cost, 1))
                m["expected"] = round(expected, 2)
                m["sim"] = speaker_sim(model, ref_emb, wav)
                content_ok = True
                if req.verify_text and task in ("TTS", "MULTILINGUAL", "BASIC", "EMOTION", "DIALECT"):
                    # 时长/电平只能抓"截头/过短"，抓不到"内容是噪音"：用 ASR 复核念出来的字
                    try:
                        heard, sim = asr_verify(wav, seg)
                        m["asr_sim"] = sim
                        m["asr_text"] = heard
                        content_ok = sim >= ASR_PASS
                        logs.append("段%d 第%d次 ASR 对率=%s «%s»" % (idx, attempt + 1, sim,
                                                                   (heard or "")[:24]))
                    except Exception as e:
                        logs.append("段%d 第%d次 ASR 校验失败（视为通过）：%s" % (idx, attempt + 1, e))
                m["content_ok"] = content_ok
                # 时长只在「严重异常」时才算失败：明显截头（<0.55×期望）或拖尾复读（>3×期望+2s）
                sev_bad = (m["duration"] < expected * DUR_SEVERE_LOW or
                           m["duration"] > expected * DUR_SEVERE_HIGH + 2.0)
                m["dur_severe"] = bool(sev_bad)
                logs.append("段%d 第%d次 %ss %sdB score=%s%s%s" % (
                    idx, attempt + 1, m["duration"], m["rms"], m["score"],
                    "" if m["sim"] is None else " sim=%s" % m["sim"],
                    " 时长严重异常" if sev_bad else ""))
                rank = (2.0 if content_ok else 0.0) + m["score"] - (1.0 if sev_bad else 0.0)
                if best is None or rank > best[0]["rank"]:
                    m["rank"] = rank
                    best = (m, wav)
                # 放行：内容 OK + 音量 OK + 时长不严重异常；轻微偏快/偏慢不再重跑
                if content_ok and m["level_ok"] and not sev_bad:
                    break
            if best is None:
                return JSONResponse(status_code=500,
                                    content={"ok": False, "error": "全部重试失败", "log": logs})
            chunks.append(best[1])
            metas.append(best[0])
            if reuse and idx == 1:
                # 官方行为：音色设计类多段时，第 1 段的生成结果当后续所有段的参考
                seg_ref_path = save_seg_wav(best[1], "reuse1")
                seg_ref_text = segments[0]
                seg_spk = None
                logs.append("音色统一：第 2 段起改用第 1 段生成结果当参考")

    if not chunks:
        return JSONResponse(status_code=400, content={"ok": False, "error": "没有可合成的内容"})

    if only is not None:
        # 单段重生成：只回这一段的音频和指标
        return {
            "ok": True,
            "mode": mode,
            "task": req.task,
            "segments": len(segments),
            "seg_index": only,
            "duration": metas[0]["duration"],
            "rms": metas[0]["rms"],
            "sim": metas[0].get("sim"),
            "score": metas[0].get("score"),
            "audio": "data:audio/wav;base64," + _wav_b64(chunks[0]),
            "dur_check": [{"seg": metas[0].get("seg"), "expected": metas[0].get("expected"),
                           "duration": metas[0]["duration"], "attempts": metas[0].get("attempt")}],
            "log": logs,
        }

    gap_sec = max(0.0, min(float(req.gap or 0.35), 1.5))
    gap = torch.zeros(chunks[0].shape[0], int(44100 * gap_sec), dtype=chunks[0].dtype)
    inter = []
    for c in chunks:
        inter.append(c)
        inter.append(gap)
    full = torch.cat(inter[:-1], dim=-1) if inter else chunks[0]
    audio_b64 = _wav_b64(full)
    seg_b64 = [_wav_b64(c) for c in chunks]
    sims = [m["sim"] for m in metas if m.get("sim") is not None]

    return {
        "ok": True,
        "mode": mode,
        "task": req.task,
        "segments": len(segments),
        "duration": round(float(full.shape[-1]) / 44100.0, 2),
        "rms": round(rms_db(full), 1),
        "sim": round(sum(sims) / len(sims), 3) if sims else None,
        "gap": gap_sec,
        "audio": "data:audio/wav;base64," + audio_b64,
        "segments_audio": seg_b64,
        "segments_meta": metas,
        "dur_check": [{"seg": m.get("seg"), "expected": m.get("expected"),
                       "duration": m["duration"], "attempts": m.get("attempt")} for m in metas],
        "log": logs,
    }


if __name__ == "__main__":
    import uvicorn
    load_model()
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="warning")






