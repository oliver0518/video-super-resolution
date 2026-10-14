# -*- coding: utf-8 -*-
"""
秋白超分 - 本地视频/图片超分工具
技术栈: Flask + ffmpeg + (可选) Real-ESRGAN ncnn
完全本地处理，不上传任何数据。
"""
import os
import sys
import uuid
import subprocess
import tempfile
import shutil
import json
import glob
import logging
import time
import threading

import cv2
import numpy as np
from flask import (
    Flask, request, jsonify, render_template, send_from_directory, send_file, abort
)

# ---------- 配置 ----------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GiB

# 日志：frozen 时也写到 ~/Library/Logs/WatermarkRemover.log 方便排查
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.expanduser("~/Library/Logs/VideoSuperRes.log"),
            mode="a",
        ) if sys.platform == "darwin" else logging.StreamHandler(),
    ],
)
log = logging.getLogger("video-superres")

# ---------- Single-instance helpers ----------
import socket
from urllib.request import Request, urlopen


def _another_instance_alive(host="127.0.0.1", port=8400, timeout=1.5):
    """探测 localhost:port 是否已被另一实例 bind/listen。
    用 connect 而不是尝试 bind，避开权限问题并允许短超时。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _signal_existing_instance(host="127.0.0.1", port=8400, timeout=3):
    """向已存在的实例发 /api/show，让它把窗口拉前台。
    失败也无所谓 —— 老实例可能刚启动还没注册路由，或对方不是本 app。
    """
    try:
        req = Request(f"http://{host}:{port}/api/show", method="GET")
        urlopen(req, timeout=timeout).read()
        return True
    except Exception as e:
        log.warning("signal existing instance failed: %s", e)
        return False


BASE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE, "uploads")
OUTPUT_DIR = os.path.join(BASE, "output")
THUMB_DIR = os.path.join(BASE, "thumbnails")
FFMPEG_DIR = os.path.join(BASE, "bin")
for d in (UPLOAD_DIR, OUTPUT_DIR, THUMB_DIR):
    os.makedirs(d, exist_ok=True)

# PyInstaller frozen 时，把可写目录重定向到 ~/Library/...
if getattr(sys, "frozen", False):
    if sys.platform == "darwin":
        user_base = os.path.expanduser("~/Library/Application Support/VideoSuperRes")
    elif sys.platform == "win32":
        user_base = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "VideoSuperRes",
        )
    else:
        user_base = os.path.expanduser("~/.local/share/video-superres")
    user_base = os.path.abspath(user_base)
    BASE = user_base
    UPLOAD_DIR = os.path.join(BASE, "uploads")
    OUTPUT_DIR = os.path.join(BASE, "output")
    THUMB_DIR = os.path.join(BASE, "thumbnails")
    for d in (UPLOAD_DIR, OUTPUT_DIR, THUMB_DIR):
        os.makedirs(d, exist_ok=True)
    log.info("data dir -> %s", BASE)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

# Real-ESRGAN ncnn-vulkan binary 路径（可选）
# 用户可放 bin/realesrgan-ncnn-vulkan (macOS/Linux) 或 bin/realesrgan-ncnn-vulkan.exe (Windows)
def _bundled_binary(name):
    """如果运行在 PyInstaller 打包的 .app/_exe 内，从 _MEIPASS 取出内嵌二进制。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    return None

_exe_suffix = ".exe" if sys.platform == "win32" else ""
_realesrgan_name = f"realesrgan-ncnn-vulkan{_exe_suffix}"
# 解析顺序：_MEIPASS (PyInstaller 内嵌) → bin/ → PATH
_candidates = [
    _bundled_binary(_realesrgan_name),
    os.path.join(FFMPEG_DIR, _realesrgan_name),
    shutil.which(_realesrgan_name),
    shutil.which("realesrgan-ncnn-vulkan"),
    shutil.which("realesrgan-ncnn-vulkan.exe"),
]
REALESRGAN = next((c for c in _candidates if c and os.path.isfile(c)), None)


def _ffmpeg_src():
    """PyInstaller 打包下 ffmpeg 可能需要从 _MEIPASS 取。"""
    p = _bundled_binary("ffmpeg" + _exe_suffix)
    return p or FFMPEG


def _ffprobe_src():
    p = _bundled_binary("ffprobe" + _exe_suffix)
    return p or FFPROBE


# ---------- 工具 ----------

ALLOWED_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
               ".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def allowed_file(name):
    if not name:
        return False
    ext = os.path.splitext(name)[1].lower()
    return ext in ALLOWED_EXT


# magic bytes 校验：mp4/png/jpg 等
MAGIC_BYTES = {
    ".mp4": [b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00 ftyp",
             b"\x00\x00\x00(ftyp", b"\x00\x00\x00,ftyp"],
    ".mov": [b"\x00\x00\x00\x14ftyp", b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp"],
    ".png": [b"\x89PNG\r\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".bmp": [b"BM"],
    ".webp": [b"RIFF"],
}


def detect_kind(path):
    """读文件头 16 字节判断格式（防扩展名欺骗）。返回小写 ext 或 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    for ext, sigs in MAGIC_BYTES.items():
        for sig in sigs:
            if head.startswith(sig):
                return ext
    # MKV/WebM: 1A 45 DF A3
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return ".mkv"
    # AVI: RIFF....AVI
    if head.startswith(b"RIFF") and b"AVI " in head[:12]:
        return ".avi"
    return None


def video_info(path):
    """ffprobe 读视频元信息。"""
    cmd = [
        _ffprobe_src(), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    info = {"width": 0, "height": 0, "fps": 0.0, "duration": 0.0,
            "frames": 0, "has_audio": False}
    try:
        data = json.loads(r.stdout or "{}")
        s = (data.get("streams") or [{}])[0]
        info["width"] = int(s.get("width") or 0)
        info["height"] = int(s.get("height") or 0)
        info["fps"] = _parse_fps(s.get("r_frame_rate") or "0/1")
        info["frames"] = int(s.get("nb_frames") or 0)
        info["duration"] = float((data.get("format") or {}).get("duration") or 0)
    except Exception as e:
        log.warning("parse ffprobe output failed: %s", e)
    # 检测音频
    ar = subprocess.run([
        _ffprobe_src(), "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", path,
    ], capture_output=True, text=True, timeout=10)
    info["has_audio"] = bool((ar.stdout or "").strip())
    return info


def _parse_fps(s):
    try:
        if "/" in s:
            n, d = s.split("/")
            return float(n) / float(d) if float(d) else 0.0
        return float(s)
    except Exception:
        return 0.0


def extract_thumbnail(src, sid, width=240):
    """抽一帧作为缩略图。视频取第 0 帧，图片直接缩放。
    不同 codec 跳帧行为不同：先试 frame index 0，再试 -ss 0.5，再试 -ss 1。"""
    try:
        thumb_path = os.path.join(THUMB_DIR, sid + ".jpg")
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
        # 清掉上次失败留下的 0 字节文件
        if os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass
        ext = os.path.splitext(src)[1].lower()
        is_video = ext in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
        if not is_video:
            cmd = [
                _ffmpeg_src(), "-y", "-i", src,
                "-frames:v", "1", "-vf", f"scale={width}:-2",
                "-q:v", "5", thumb_path,
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=20)
            if r.returncode == 0 and os.path.exists(thumb_path):
                return thumb_path
            log.warning("thumbnail image extract failed: %s",
                        (r.stderr or b"")[-300:].decode(errors="ignore") if hasattr(r.stderr, "decode") else r.stderr)
            return None

        # 视频：依次尝试不同的取帧位置，跳过黑帧
        attempts = [
            # (seek, label) - 顺序：先无 seek（首帧），再短暂 seek，再更远
            ("0",   "first"),
            ("0.5", "0.5s"),
            ("1",   "1s"),
            ("2",   "2s"),
        ]
        for ss, _label in attempts:
            try:
                if ss == "0":
                    cmd = [
                        _ffmpeg_src(), "-y", "-i", src,
                        "-frames:v", "1", "-vf", f"scale={width}:-2",
                        "-q:v", "5", thumb_path,
                    ]
                else:
                    cmd = [
                        _ffmpeg_src(), "-y", "-ss", ss, "-i", src,
                        "-frames:v", "1", "-vf", f"scale={width}:-2",
                        "-q:v", "5", thumb_path,
                    ]
                r = subprocess.run(cmd, capture_output=True, timeout=15)
                if r.returncode == 0 and os.path.exists(thumb_path) \
                        and os.path.getsize(thumb_path) > 0:
                    return thumb_path
                # 清理失败产物
                if os.path.exists(thumb_path):
                    try:
                        os.remove(thumb_path)
                    except OSError:
                        pass
            except Exception:
                continue
        log.warning("thumbnail extract failed for all attempts: %s", src)
        return None
    except Exception as e:
        log.warning("extract_thumbnail failed: %s", e)
        return None


def safe_move(src, dst):
    """原子移动文件（先 rename 再 fallback copy+remove）。"""
    try:
        os.rename(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        os.remove(src)


def output_name_for(original_name, suffix="_超分"):
    stem, ext = os.path.splitext(original_name)
    if not ext:
        ext = ".mp4"
    return f"{stem}{suffix}{ext}"


# ---------- 超分核心 ----------

def upscale_image(src_path, out_path, scale=2, method="ffmpeg"):
    """
    单张图片超分。
    method: ffmpeg | realesrgan
    """
    if method == "realesrgan" and REALESRGAN:
        # 关键策略：realesrgan-x4plus 是 4x 训练。
        # 直接 -s 2/3 时，binary 先按 4x 重建再降采样到目标，
        # 降采样会扭曲小目标（如房子、人脸细节）。
        # 修正：总是 4x 重建，然后 lanczos downscale 到用户要求的 scale —— 保留所有 AI 重建的细节。
        info = _ffprobe_image(src_path)
        w, h = info["width"], info["height"]
        if w <= 0 or h <= 0:
            raise RuntimeError("invalid image dimensions")
        bin_dir = os.path.dirname(REALESRGAN)
        models_dir = os.path.join(bin_dir, "models")
        # AI 跑 4x 到临时文件
        tmp_4x = out_path + ".ai4x.png"
        cmd = [
            REALESRGAN,
            "-i", src_path,
            "-o", tmp_4x,
            "-s", "4",
            "-m", models_dir,
            "-n", "realesrgan-x4plus",
            "-g", "0",
            "-f", "png",  # 中间用 png，避免 JPEG 重编码损失
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            err = (r.stderr.decode("utf-8", errors="ignore") if isinstance(r.stderr, bytes) else str(r.stderr or ""))[:500]
            raise RuntimeError(f"realesrgan failed: {err}")
        # lanczos downscale / 等比缩放到目标 scale
        target_w = max(1, round(w * scale))
        target_h = max(1, round(h * scale))
        down_cmd = [
            _ffmpeg_src(), "-y", "-i", tmp_4x,
            "-vf", f"scale={target_w}:{target_h}:flags=lanczos",
            "-frames:v", "1",
            out_path,
        ]
        try:
            dr = subprocess.run(down_cmd, capture_output=True, text=True, timeout=60)
            if dr.returncode != 0:
                raise RuntimeError(f"realesrgan post-scale failed: {(dr.stderr or '')[-300:]}")
        finally:
            try:
                os.remove(tmp_4x)
            except OSError:
                pass
        return
    # fallback: ffmpeg lanczos scale
    info = _ffprobe_image(src_path)
    w, h = info["width"], info["height"]
    if w <= 0 or h <= 0:
        raise RuntimeError("invalid image dimensions")
    nw, nh = w * scale, h * scale
    cmd = [
        _ffmpeg_src(), "-y", "-i", src_path,
        "-vf", f"scale={nw}:{nh}:flags=lanczos",
        "-frames:v", "1",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg upscale failed: {r.stderr[-500:] if r.stderr else ''}")


def _ffprobe_image(path):
    cmd = [
        _ffprobe_src(), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    info = {"width": 0, "height": 0}
    try:
        data = json.loads(r.stdout or "{}")
        s = (data.get("streams") or [{}])[0]
        info["width"] = int(s.get("width") or 0)
        info["height"] = int(s.get("height") or 0)
    except Exception:
        pass
    return info


def _parse_fps_progress(line, duration=0.0):
    """解析 ffmpeg -progress 输出。返回 0.0~1.0。"""
    global _FMPG_PROGRESS
    try:
        line = line.decode("utf-8", errors="ignore").strip() if isinstance(line, bytes) else line.strip()
    except Exception:
        return
    if line.startswith("out_time_ms="):
        try:
            usec = int(line.split("=", 1)[1].strip())
            if duration > 0:
                p = usec / (duration * 1_000_000)
                _FMPG_PROGRESS["p"] = min(max(p, _FMPG_PROGRESS["p"]), 0.99)
        except Exception:
            pass
    elif line.startswith("progress=continue") or line.startswith("progress=end"):
        pass


_FMPG_PROGRESS = {"p": 0.0}


def upscale_video(src_path, out_path, scale=2, method="ffmpeg", progress_cb=None):
    """
    视频超分。
    - method=ffmpeg : 直接 ffmpeg 转码，scale filter lanczos，音轨 copy（极速）
    - method=realesrgan : 抽帧 → 单帧超分 → 合回（realesrgan 只支持 image）
    """
    info = video_info(src_path)
    if info["width"] <= 0:
        raise RuntimeError("invalid video dimensions")
    if info["fps"] <= 0:
        info["fps"] = 25.0
    w, h = info["width"], info["height"]
    nw, nh = w * scale, h * scale
    duration = float(info.get("duration") or 0.0)

    # ---- path A: ffmpeg 直接转码（推荐，速度快 100x） ----
    if method == "ffmpeg":
        cmd = [
            _ffmpeg_src(), "-y", "-i", src_path,
            "-vf", f"scale={nw}:{nh}:flags=lanczos",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "medium", "-crf", "20",
            "-c:a", "copy",  # 直接 copy 音轨
        ]
        # 带进度输出
        if progress_cb and duration > 0:
            cmd += ["-progress", "pipe:1", "-nostats"]
            try:
                proc = subprocess.Popen(cmd + [out_path],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1)
                assert proc.stdout is not None
                for line in proc.stdout:
                    _parse_fps_progress(line, duration)
                    if line.startswith("progress="):
                        progress_cb(_FMPG_PROGRESS["p"])
                proc.wait()
                if proc.returncode != 0:
                    err = (proc.stderr.read() if proc.stderr else "")[-500:]
                    raise RuntimeError(f"ffmpeg video upscale failed: {err}")
                progress_cb(1.0)
                return
            except Exception as e:
                log.exception("ffmpeg progress path failed: %s", e)
                # fall through to non-progress
        # 不带进度（fallback）
        cmd.append(out_path)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg video upscale failed: {(r.stderr or '')[-500:]}")
        if progress_cb:
            progress_cb(1.0)
        return

    # ---- path B: realesrgan：抽帧 → 逐帧 → 合回（仅 AI 模式必需） ----
    tmpdir = tempfile.mkdtemp(prefix="superres_", dir=OUTPUT_DIR)
    try:
        # 1) 抽音轨（如果有）
        audio_tmp = os.path.join(tmpdir, "audio.aac")
        if info["has_audio"]:
            ar = subprocess.run([
                _ffmpeg_src(), "-y", "-i", src_path,
                "-vn", "-acodec", "copy", audio_tmp,
            ], capture_output=True, timeout=120)
            if ar.returncode != 0:
                log.warning("audio extract failed, continuing without audio")

        # 2) 抽帧到 PNG
        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        frame_pattern = os.path.join(frames_dir, "f_%06d.png")
        fr = subprocess.run([
            _ffmpeg_src(), "-y", "-i", src_path,
            "-vf", f"scale={w}:{h}",  # 保持原尺寸
            "-fps_mode", "passthrough",
            frame_pattern,
        ], capture_output=True, timeout=600)
        if fr.returncode != 0:
            raise RuntimeError("frame extract failed")

        # 列帧
        frames = sorted(glob.glob(os.path.join(frames_dir, "f_*.png")))
        total = len(frames)
        if total == 0:
            raise RuntimeError("no frames extracted")
        log.info("upscale %d frames, method=%s, scale=%dx", total, method, scale)

        # 3) 逐帧超分
        upscaled_dir = os.path.join(tmpdir, "upscaled")
        os.makedirs(upscaled_dir, exist_ok=True)
        for i, fp in enumerate(frames, 1):
            out = os.path.join(upscaled_dir, os.path.basename(fp))
            upscale_image(fp, out, scale=scale, method=method)
            if progress_cb:
                p = i / total
                progress_cb(min(p, 0.99))

        # 4) 合并回视频
        upscaled_pattern = os.path.join(upscaled_dir, "f_%06d.png")
        cmd = [
            _ffmpeg_src(), "-y",
            "-framerate", str(info["fps"]),
            "-i", upscaled_pattern,
        ]
        if os.path.isfile(audio_tmp) and os.path.getsize(audio_tmp) > 0:
            cmd += ["-i", audio_tmp, "-map", "0:v", "-map", "1:a",
                    "-c:a", "aac", "-b:a", "128k",
                    "-shortest"]
        else:
            cmd += ["-map", "0:v"]
        cmd += [
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "medium", "-crf", "20",
            out_path,
        ]
        mr = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if mr.returncode != 0:
            raise RuntimeError(f"video merge failed: {(mr.stderr or '')[-500:]}")
        if progress_cb:
            progress_cb(1.0)
        log.info("video super-res done -> %s", out_path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------- 历史 ----------

import threading as _threading
HISTORY_LOCK = _threading.Lock()
HISTORY_FILE = os.path.join(BASE, "history.json")
HISTORY_MAX = 30


def _load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("load history failed: %s", e)
        return []


def _save_history(items):
    try:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_FILE)
    except Exception as e:
        log.warning("save history failed: %s", e)


def add_history(record):
    with HISTORY_LOCK:
        items = _load_history()
        items.insert(0, record)
        if len(items) > HISTORY_MAX:
            for old in items[HISTORY_MAX:]:
                for key in ("srcPath", "outputPath", "thumbPath"):
                    p = old.get(key)
                    if p and os.path.isfile(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
            items = items[:HISTORY_MAX]
        _save_history(items)


def remove_history(record_id):
    with HISTORY_LOCK:
        items = _load_history()
        kept = []
        removed = None
        for it in items:
            if it.get("id") == record_id and removed is None:
                removed = it
            else:
                kept.append(it)
        if removed is not None:
            _save_history(kept)
            for key in ("srcPath", "outputPath", "thumbPath"):
                p = removed.get(key)
                if p and os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
        return removed is not None


def output_name_for_record(it):
    orig = it.get("originalName", "video.mp4")
    out = it.get("outputName")
    if out:
        return out
    return output_name_for(orig)


# ---------- 路由 ----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/uploads/<name>")
def uploaded(name):
    name = os.path.basename(name)
    # exact match first (e.g. <sid>.mp4), then glob with double extensions (e.g. <sid>.png.meta)
    cand = os.path.join(UPLOAD_DIR, name)
    if os.path.isfile(cand):
        full = cand
    else:
        matches = [m for m in glob.glob(cand + ".*")
                   if not m.endswith(".meta") and os.path.isfile(m)]
        if not matches:
            abort(404)
        full = matches[0]
    if not os.path.exists(full):
        abort(404)
    ext = os.path.splitext(full)[1].lower()
    if ext in (".mp4", ".mov", ".mkv", ".webm", ".m4v"):
        mime = "video/mp4"
    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        mime = "image/" + ext.lstrip(".")
        if ext == ".jpg":
            mime = "image/jpeg"
    else:
        mime = "application/octet-stream"
    return send_file(full, mimetype=mime, conditional=True)


@app.route("/api/upload", methods=["POST"])
def upload():
    files = request.files.getlist("video")
    if not files:
        return jsonify({"error": "未收到文件"}), 400

    items = []
    for f in files:
        if not f or not f.filename:
            continue
        if not allowed_file(f.filename):
            items.append({"filename": f.filename, "error": "不支持的格式"})
            continue
        sid = uuid.uuid4().hex
        ext = os.path.splitext(f.filename)[1].lower()
        src = os.path.join(UPLOAD_DIR, sid + ext)
        try:
            f.save(src)
            kind = detect_kind(src)
            if kind is None:
                os.remove(src)
                items.append({"filename": f.filename, "error": "文件内容与扩展名不符"})
                continue
            # 图片/视频分别解析
            if kind in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"):
                info = video_info(src)
                kind_label = "video"
            else:
                ii = _ffprobe_image(src)
                info = {"width": ii["width"], "height": ii["height"],
                        "fps": 0, "duration": 0, "frames": 1, "has_audio": False,
                        "kind": "image"}
                kind_label = "image"
            if info["width"] <= 0 or info["height"] <= 0:
                os.remove(src)
                items.append({"filename": f.filename, "error": "无法解析"})
                continue
            log.info("upload sid=%s kind=%s %dx%d %s", sid, kind_label,
                     info["width"], info["height"], f.filename)
            items.append({
                "id": sid,
                "filename": f.filename,
                "src": f"/uploads/{sid}{kind}",
                "info": info,
                "kind": kind_label,
                "thumbUrl": f"/api/thumb/{sid}",
            })
            # meta 文件存 originalName
            try:
                with open(src + ".meta", "w", encoding="utf-8") as mf:
                    json.dump({"originalName": f.filename}, mf, ensure_ascii=False)
            except OSError:
                pass
            # 缩略图
            try:
                extract_thumbnail(src, sid)
            except Exception as e:
                log.warning("thumbnail extract error: %s", e)
        except Exception as e:
            log.exception("upload %s failed: %s", f.filename, e)
            items.append({"filename": f.filename, "error": str(e)})

    if len(items) == 1:
        only = items[0]
        if "id" in only:
            return jsonify({
                "id": only["id"], "filename": only["filename"],
                "src": only["src"], "info": only["info"], "kind": only["kind"],
            })
        return jsonify({"error": only.get("error", "上传失败")}), 400
    return jsonify({"items": items})


# ---------- 后台任务系统 ----------
import threading as _threading

TASKS = {}                # task_id -> {vid, progress, status, error, result_url, result_id}
TASKS_LOCK = _threading.Lock()
TASKS_MAX_AGE = 60 * 30   # 30 分钟后清理


def _cleanup_tasks():
    """清理 30 分钟前完成的任务。"""
    now = time.time()
    with TASKS_LOCK:
        stale = [tid for tid, t in TASKS.items()
                 if t.get("finished_at") and now - t["finished_at"] > TASKS_MAX_AGE]
        for tid in stale:
            TASKS.pop(tid, None)


def _run_process_task(task_id, vid, scale, method, src, public_out, is_image, original_name):
    """后台线程入口：真正跑 upscale + 写历史。"""
    def cb(p):
        with TASKS_LOCK:
            if task_id in TASKS:
                TASKS[task_id]["progress"] = p

    try:
        with TASKS_LOCK:
            TASKS[task_id]["status"] = "processing"
            TASKS[task_id]["progress"] = 0.02  # 立即让前端看到动起来

        if is_image:
            upscale_image(src, public_out, scale=scale, method=method)
        else:
            upscale_video(src, public_out, scale=scale, method=method, progress_cb=cb)

        # 写历史（含原始 + 输出两侧的尺寸 / 大小 / 时长，便于卡片对比）
        thumb_path = os.path.join(THUMB_DIR, vid + ".jpg")
        out_size = os.path.getsize(public_out) if os.path.isfile(public_out) else 0
        orig_size = os.path.getsize(src) if os.path.isfile(src) else 0
        if is_image:
            info = _ffprobe_image(public_out)
            ii_orig = _ffprobe_image(src)
            add_history({
                "id": vid, "originalName": original_name, "outputName": output_name_for(original_name),
                "processedAt": int(time.time()),
                # 输出侧
                "width": info["width"], "height": info["height"],
                "duration": 0.0, "size": out_size, "kind": "image",
                # 原始侧（前端对比展示用）
                "originalWidth": ii_orig["width"], "originalHeight": ii_orig["height"],
                "originalSize": orig_size, "originalDuration": 0.0,
                "srcPath": src, "outputPath": public_out,
                "thumbPath": thumb_path if os.path.isfile(thumb_path) else None,
                "scale": scale, "method": method,
            })
        else:
            vinfo = video_info(public_out)
            vorig = video_info(src)
            add_history({
                "id": vid, "originalName": original_name, "outputName": output_name_for(original_name),
                "processedAt": int(time.time()),
                # 输出侧
                "width": vinfo["width"], "height": vinfo["height"],
                "duration": vinfo["duration"], "size": out_size, "kind": "video",
                # 原始侧
                "originalWidth": vorig["width"], "originalHeight": vorig["height"],
                "originalSize": orig_size, "originalDuration": vorig["duration"],
                "srcPath": src, "outputPath": public_out,
                "thumbPath": thumb_path if os.path.isfile(thumb_path) else None,
                "scale": scale, "method": method,
            })

        with TASKS_LOCK:
            TASKS[task_id].update({
                "status": "done", "progress": 1.0,
                "result_id": vid,
                "result_url": f"/output/{os.path.basename(public_out)}",
                "finished_at": time.time(),
            })
        log.info("task %s done for vid=%s", task_id, vid)
    except Exception as e:
        log.exception("task %s failed: %s", task_id, e)
        with TASKS_LOCK:
            TASKS[task_id].update({
                "status": "error",
                "error": str(e),
                "finished_at": time.time(),
            })


@app.route("/api/process", methods=["POST"])
def process():
    """异步：POST 立即返回 task_id，前端轮询 /api/task/<task_id>。"""
    data = request.get_json(silent=True) or {}
    vid = data.get("id")
    scale = int(data.get("scale", 2))
    method = data.get("method", "ffmpeg")
    if scale not in (2, 3, 4):
        scale = 2
    if method not in ("ffmpeg", "realesrgan"):
        method = "ffmpeg"
    if method == "realesrgan" and not REALESRGAN:
        return jsonify({"error": "realesrgan-ncnn-vulkan 未安装；请在 bin/ 目录放入 Real-ESRGAN 二进制后重试，或选择 ffmpeg 算法"}), 400
    if not vid:
        return jsonify({"error": "缺少 id"}), 400

    matches = [m for m in glob.glob(os.path.join(UPLOAD_DIR, vid) + ".*")
               if not m.endswith(".meta")]
    if not matches:
        return jsonify({"error": "源文件不存在"}), 404
    src = matches[0]
    src_ext = os.path.splitext(src)[1].lower()
    is_image = src_ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp")

    # 读 originalName
    original_name = None
    meta_path = src + ".meta"
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                original_name = (json.load(mf) or {}).get("originalName")
        except Exception:
            pass
    if not original_name:
        original_name = os.path.basename(src)

    out_basename = output_name_for(original_name)
    public_out = os.path.join(OUTPUT_DIR, f"{vid}__{out_basename}")

    # 清理陈旧任务
    try:
        _cleanup_tasks()
    except Exception:
        pass

    task_id = uuid.uuid4().hex
    with TASKS_LOCK:
        TASKS[task_id] = {
            "vid": vid, "scale": scale, "method": method, "kind": "image" if is_image else "video",
            "status": "queued", "progress": 0.0, "error": None,
            "result_id": None, "result_url": None,
            "created_at": time.time(), "finished_at": None,
        }

    t = _threading.Thread(
        target=_run_process_task,
        args=(task_id, vid, scale, method, src, public_out, is_image, original_name),
        daemon=True, name=f"upscale-{task_id[:8]}",
    )
    t.start()

    return jsonify({"task_id": task_id, "vid": vid}), 202


@app.route("/api/task/<task_id>", methods=["GET"])
def task_status(task_id):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
    if not t:
        return jsonify({"error": "task 不存在"}), 404
    return jsonify({
        "task_id": task_id, "vid": t["vid"], "kind": t.get("kind"),
        "status": t["status"],          # queued / processing / done / error
        "progress": float(t["progress"]),
        "error": t.get("error"),
        "result_id": t.get("result_id"),
        "result_url": t.get("result_url"),
    })


@app.route("/output/<name>")
def output(name):
    name = os.path.basename(name)
    p = os.path.join(OUTPUT_DIR, name)
    if not os.path.isfile(p):
        abort(404)
    return send_file(p, as_attachment=True, download_name=name)


@app.route("/api/thumb/<sid>")
def api_thumb(sid):
    if not sid.isalnum():
        abort(404)
    p = os.path.join(THUMB_DIR, sid + ".jpg")
    if not os.path.isfile(p):
        for ext in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
                    ".png", ".jpg", ".jpeg", ".bmp", ".webp"):
            src = os.path.join(UPLOAD_DIR, sid + ext)
            if os.path.isfile(src):
                extract_thumbnail(src, sid)
                break
    if not os.path.isfile(p):
        from flask import Response
        return Response(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            mimetype="image/png",
        )
    return send_file(p, mimetype="image/jpeg", max_age=3600)


def _find_history_item(rid):
    for it in _load_history():
        if it.get("id") == rid:
            return it
    return None


@app.route("/api/history", methods=["GET"])
def api_history_list():
    items = _load_history()
    out = []
    for it in items:
        # 旧历史项可能没有原始尺寸字段，按需懒加载（只触发 stat / ffprobe）
        orig_w = it.get("originalWidth") or 0
        orig_h = it.get("originalHeight") or 0
        orig_size = it.get("originalSize") or 0
        orig_dur = it.get("originalDuration") or 0.0
        if (not orig_w or not orig_h) and it.get("srcPath") and os.path.isfile(it["srcPath"]):
            try:
                if (it.get("kind") or "video") == "image":
                    ii = _ffprobe_image(it["srcPath"])
                    orig_w, orig_h = ii["width"], ii["height"]
                else:
                    vv = video_info(it["srcPath"])
                    orig_w, orig_h = vv["width"], vv["height"]
                    orig_dur = orig_dur or vv["duration"]
                if not orig_size:
                    orig_size = os.path.getsize(it["srcPath"])
            except Exception:
                pass
        out.append({
            "id": it.get("id"),
            "originalName": it.get("originalName", "video.mp4"),
            "outputName": it.get("outputName") or output_name_for(it.get("originalName", "video.mp4")),
            "processedAt": it.get("processedAt"),
            # 输出侧
            "width": it.get("width", 0),
            "height": it.get("height", 0),
            "duration": it.get("duration", 0.0),
            "size": it.get("size", 0),
            "kind": it.get("kind", "video"),
            # 原始侧
            "originalWidth": orig_w,
            "originalHeight": orig_h,
            "originalSize": orig_size,
            "originalDuration": orig_dur,
            # 元信息
            "scale": it.get("scale", 2),
            "method": it.get("method", "ffmpeg"),
            "thumbUrl": f"/api/thumb/{it.get('id')}",
            "downloadUrl": f"/api/history/{it.get('id')}/download",
            "originalUrl": f"/api/history/{it.get('id')}/original",
            "outputUrl": f"/api/history/{it.get('id')}/view",
        })
    return jsonify(out)


@app.route("/api/history/<rid>/download")
def api_history_download(rid):
    it = _find_history_item(rid)
    if not it:
        abort(404)
    p = it.get("outputPath")
    if not p or not os.path.isfile(p):
        abort(404)
    return send_file(
        p, as_attachment=True,
        download_name=it.get("outputName") or output_name_for(it.get("originalName", "video.mp4")),
    )


@app.route("/api/history/<rid>/view")
def api_history_view(rid):
    it = _find_history_item(rid)
    if not it:
        abort(404)
    p = it.get("outputPath")
    if not p or not os.path.isfile(p):
        abort(404)
    kind = it.get("kind", "video")
    if kind == "image":
        return send_file(p, mimetype="image/jpeg", conditional=True)
    return send_file(p, mimetype="video/mp4", conditional=True)


@app.route("/api/history/<rid>/original")
def api_history_original(rid):
    """返回该条历史的原始文件（供 A/B 对比预览）。"""
    it = _find_history_item(rid)
    if not it:
        abort(404)
    p = it.get("srcPath")
    if not p or not os.path.isfile(p):
        abort(404)
    ext = os.path.splitext(p)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        mime = "image/jpeg" if ext == ".jpg" else f"image/{ext.lstrip('.')}"
    elif ext in (".mp4", ".m4v"):
        mime = "video/mp4"
    elif ext == ".mov":
        mime = "video/quicktime"
    elif ext == ".webm":
        mime = "video/webm"
    elif ext == ".mkv":
        mime = "video/x-matroska"
    elif ext == ".avi":
        mime = "video/x-msvideo"
    else:
        mime = "application/octet-stream"
    return send_file(p, mimetype=mime, conditional=True)


@app.route("/api/history/<rid>", methods=["DELETE"])
def api_history_delete(rid):
    ok = remove_history(rid)
    if not ok:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify({"ok": True})


@app.route("/api/backend")
def api_backend():
    """报告当前可用后端。"""
    return jsonify({
        "ffmpeg": True,
        "realesrgan": bool(REALESRGAN),
        "realesrgan_path": REALESRGAN,
    })


@app.route("/api/show")
def api_show():
    """Single-instance focus: 另一实例启动后调这里，把当前窗口拉前台。

    Cocoa UI 操作必须在主线程 (NSApp run loop) 上做；
    从 Flask worker thread 通过 PyObjCTools.AppHelper.callAfter 派发回主线程。
    """
    from PyObjCTools.AppHelper import callAfter

    def _focus():
        try:
            from AppKit import NSApp
            NSApp.activateIgnoringOtherApps_(True)
            for w in NSApp.windows():
                try:
                    w.makeKeyAndOrderFront_(None)
                except Exception:
                    pass
        except Exception as e:
            log.warning("api_show focus failed: %r", e)

    callAfter(_focus)
    return jsonify({"ok": True})


# ---------- 错误处理 ----------

@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "上传文件过大（上限 1 GiB）"}), 413


# ---------- 入口 ----------

if __name__ == "__main__":
    import threading
    import time
    import webbrowser

    port = int(os.environ.get("DEPLOY_RUN_PORT", "8400"))
    host = "127.0.0.1"

    log.info("starting video-superres on http://%s:%d (frozen=%s, realesrgan=%s)",
             host, port, getattr(sys, "frozen", False), bool(REALESRGAN))

    # Single-instance：端口被占说明老进程还活着（窗口可能因 hide 看不见）。
    # 给老进程发 /api/show 把窗口拉前台，然后干净退出，避免 Address-already-in-use 闪退。
    if _another_instance_alive(host, port):
        log.info("another instance already running on %s:%d — signaling & quitting",
                 host, port)
        _signal_existing_instance(host, port)
        sys.exit(0)

    if getattr(sys, "frozen", False):
        from werkzeug.serving import make_server

        server = make_server(host, port, app, threaded=True)
        server_thread = threading.Thread(
            target=server.serve_forever, name="flask-server", daemon=True
        )
        server_thread.start()

        try:
            import webview
        except Exception as exc:
            log.error("import pywebview failed (%s) — fall back to webbrowser", exc)
            webbrowser.open(f"http://{host}:{port}")
            server.serve_forever()
        else:
            class Api:
                def save_as(self, result_id):
                    out_path = os.path.join(OUTPUT_DIR, result_id + "__")
                    # 实际匹配（result_id 是 sid，文件命名 = <sid>__<outputName>）
                    candidates = glob.glob(os.path.join(OUTPUT_DIR, result_id + "__*"))
                    if not candidates:
                        return None
                    src = candidates[0]
                    history_item = _find_history_item(result_id)
                    default = "超分_" + (history_item["originalName"]
                                            if history_item else "video.mp4")
                    try:
                        result = window.create_file_dialog(
                            webview.SAVE_DIALOG,
                            save_filename=default,
                            file_types=("视频/图片 (*.mp4;*.png;*.jpg)", "所有文件 (*)"),
                        )
                    except Exception as e:
                        log.warning("create_file_dialog failed: %s", e)
                        return None
                    target = None
                    if isinstance(result, (tuple, list)) and result:
                        target = result[0]
                    elif isinstance(result, str):
                        target = result
                    if not target:
                        return None
                    try:
                        shutil.copy2(src, target)
                        return {"path": target, "name": os.path.basename(target)}
                    except Exception as e:
                        return {"error": str(e)}

                def save_all(self, ids):
                    if not ids:
                        return None
                    try:
                        result = window.create_file_dialog(
                            webview.FOLDER_DIALOG,
                            directory=os.path.expanduser("~/Downloads"),
                        )
                    except Exception as e:
                        log.warning("FOLDER_DIALOG failed: %s", e)
                        return None
                    target_dir = None
                    if isinstance(result, (tuple, list)) and result:
                        target_dir = result[0]
                    elif isinstance(result, str):
                        target_dir = result
                    if not target_dir:
                        return None
                    saved, failed = [], []
                    for rid in ids:
                        it = _find_history_item(rid)
                        if not it:
                            failed.append(rid)
                            continue
                        src = it.get("outputPath")
                        if not src or not os.path.isfile(src):
                            failed.append(rid)
                            continue
                        out_name = it.get("outputName") or output_name_for(
                            it.get("originalName", os.path.basename(src))
                        )
                        dst = os.path.join(target_dir, out_name)
                        if os.path.exists(dst):
                            stem, ext = os.path.splitext(out_name)
                            for k in range(1, 1000):
                                cand = os.path.join(target_dir, f"{stem}_{k}{ext}")
                                if not os.path.exists(cand):
                                    dst = cand
                                    break
                        try:
                            shutil.copy2(src, dst)
                            saved.append(os.path.basename(dst))
                        except Exception as e:
                            log.warning("save_all %s failed: %s", rid, e)
                            failed.append(rid)
                    return {"dir": target_dir, "count": len(saved),
                            "saved": saved, "failed": failed}

                def quit_app(self):
                    # 走和用户主动 Quit 完全一致的路径 (applicationShouldTerminate:)
                    # 不直接 destroy_window，避免和正在执行的 close 事件竞态
                    from AppKit import NSApp
                    NSApp.terminate(None)

            window = webview.create_window(
                title="秋白超分",
                url=f"http://{host}:{port}",
                width=960, height=760,
                resizable=True, min_size=(640, 520), text_select=True,
                js_api=Api(),
            )
            api_methods = [m for m in dir(window._js_api) if not m.startswith('_') and callable(getattr(window._js_api, m))]
            log.info("js_api injected, methods: %s", api_methods)

            # 关窗 = 隐藏（不退出进程）；
            # 但用户从 Dock / Cmd+Q 真退出时 → 必须让 window 真的关，不能再 hide
            _app_is_quitting = [False]   # 列表就地修改 closure 共享引用

            def on_closing():
                if _app_is_quitting[0]:
                    log.info("window closing: app is quitting — allow destroy")
                    return True   # 不拦截 → window 真的关 → app 退出
                log.info("window closing intercepted — hide")
                window.hide()
                return False
            try:
                window.events.closing += on_closing
            except Exception as e:
                log.warning("set closing handler failed: %s", e)

            if sys.platform == "darwin":
                try:
                    import webview.platforms.cocoa as cocoa_platform
                    # pywebview 4+ 把 AppDelegate 嵌在 BrowserView 类里，
                    # 不是模块顶层。先查顶层，找不到再查 BrowserView。
                    _AppDelegate = (
                        getattr(cocoa_platform, "AppDelegate", None)
                        or getattr(cocoa_platform, "_AppDelegate", None)
                        or getattr(getattr(cocoa_platform, "BrowserView", None), "AppDelegate", None)
                    )
                    if _AppDelegate is not None:
                        import objc

                        # 1) 阻止 NSApp 在所有 window 关闭时退出（用户点 X 隐藏而非退出）
                        try:
                            def _keep_alive(self, _sender):
                                return False
                            objc.classAddMethod(
                                _AppDelegate,
                                b"applicationShouldTerminateAfterLastWindowClosed:",
                                _keep_alive,
                            )
                            log.info("AppDelegate patched: keep alive after window close")
                        except Exception as e:
                            log.warning("patch AppDelegate (keep alive) failed: %s", e)

                        # 2) 用户主动 Quit (Cmd+Q / Dock 右击「退出」/ 菜单) →
                        #    设置 _app_is_quitting 让 on_closing 真的关闭 window
                        #    返回 NSTerminateNow (=1)
                        #
                        # !! 关键 !!
                        # pywebview 的 AppDelegate 已经实现了 applicationShouldTerminate_，
                        # objc.classAddMethod 在 selector 已存在时是 no-op（不报错但也不生效）。
                        # 所以必须用「Python subclass + setDelegate_ swap」才能真正覆盖。
                        try:
                            import Foundation as _Foundation_mod
                            YES = _Foundation_mod.YES

                            class _QuittingAppDelegate(_AppDelegate):
                                """Subclass of pywebview's AppDelegate that actually quits.

                                pywebview 默认 implementationShouldTerminate_ 会调用
                                BrowserView.should_close() 跑 on_closing，而 on_closing
                                被设计成「hide 窗口」（X 按钮行为），导致 Quit 路径被吃掉。
                                我们直接返回 NSTerminateNow，跳过 should_close：
                                      - _app_is_quitting 标志置位 → on_closing 看到会放行
                                      - 应用直接走 terminate 流程，窗口全部关闭
                                """
                                def applicationShouldTerminate_(self, app):
                                    _app_is_quitting[0] = True
                                    log.info("applicationShouldTerminate → user quit, allowing")
                                    return YES

                            # 用 subclass 实例替换 NSApp 的 delegate
                            from AppKit import NSApp as _NSApp
                            new_delegate = _QuittingAppDelegate.alloc().init()
                            if new_delegate is not None:
                                _NSApp.setDelegate_(new_delegate)
                                log.info("AppDelegate patched: subclass overrides applicationShouldTerminate_")
                            else:
                                log.warning("AppDelegate subclass alloc/init returned None")
                        except Exception as e:
                            log.warning("patch AppDelegate (should_terminate via subclass) failed: %s", e)

                        # 3) Dock 点击重新显示窗口 —— 必须用 applicationShouldHandleReopen:hasVisibleWindows:
                        #    否则之前用的 NSApplicationDidBecomeActiveNotification 在 app 没退出的情况下不会触发
                        try:
                            def _reopen_has_windows(self, _sender, _flag):
                                try:
                                    window.show()
                                    NSApp.activateIgnoringOtherApps_(True)
                                except Exception as exc:
                                    log.warning("reopen show failed: %s", exc)
                                return True
                            objc.classAddMethod(
                                _AppDelegate,
                                b"applicationShouldHandleReopen:hasVisibleWindows:",
                                _reopen_has_windows,
                            )
                            log.info("AppDelegate patched: Dock click reopens window")
                        except Exception as e:
                            log.warning("patch AppDelegate (reopen) failed: %s", e)

                    # 兜底：监听 NSApplicationDidBecomeActiveNotification（用户切换前台时同步）
                    try:
                        from Foundation import (
                            NSNotificationCenter,
                            NSApplicationDidBecomeActiveNotification,
                        )
                        from AppKit import NSApp

                        def _on_active(_notification):
                            try:
                                if window.hidden:
                                    window.show()
                                    NSApp.activateIgnoringOtherApps_(True)
                            except Exception:
                                pass
                        NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                            NSApplicationDidBecomeActiveNotification,
                            None, None, _on_active,
                        )
                    except Exception as e:
                        log.warning("became-active observer failed: %s", e)
                except Exception as e:
                    log.warning("macOS Dock handler setup failed: %s", e)

            webview.start()
            log.info("NSApp exited, shutting down")
            try:
                server.shutdown()
            except Exception:
                pass
    else:
        def _open_browser():
            time.sleep(1.5)
            try:
                webbrowser.open(f"http://{host}:{port}")
            except Exception:
                pass
        threading.Thread(target=_open_browser, daemon=True).start()
        log.info("starting video-superres on http://%s:%d", host, port)
        app.run(host=host, port=port, debug=False, threaded=True)
