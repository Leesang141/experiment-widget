"""实验描述：框架自动识别 + 想法/备注的人工标注。

数据保存在 ``data/descriptions.json``（按“文件夹 + 脚本”指纹索引），
框架识别会读取项目根目录下的 requirements / pyproject / environment 等文件。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .util import basename, log_line


# 框架关键词（按优先级从前到后）
_FRAMEWORK_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("vLLM", ("vllm",)),
    ("OpenMMLab", ("mmcv", "mmdet", "mmseg", "mmpose", "mmyolo", "mmengine")),
    ("Ultralytics", ("ultralytics", "yolov8", "yolo11")),
    ("Diffusers", ("diffusers",)),
    ("Stable-Baselines3", ("stable-baselines3", "stable_baselines3", "sb3")),
    ("HuggingFace", ("transformers", "huggingface", "peft", "trl", "datasets")),
    ("DeepSpeed", ("deepspeed",)),
    ("Lightning", ("lightning", "pytorch-lightning", "pytorch_lightning")),
    ("PyTorch", ("torch", "pytorch", "torchvision", "torchaudio")),
    ("TensorFlow", ("tensorflow", "tf.keras", "tensorboard")),
    ("JAX", ("jax", "flax", "optax", "haiku")),
    ("Keras", ("keras",)),
    ("PaddlePaddle", ("paddlepaddle", "paddle")),
    ("MindSpore", ("mindspore",)),
    ("scikit-learn", ("scikit-learn", "sklearn")),
    ("XGBoost", ("xgboost",)),
    ("LightGBM", ("lightgbm",)),
    ("CatBoost", ("catboost",)),
    ("Ray", ("ray.tune", "ray[", "ray ")),
    ("OpenCV", ("opencv", "cv2")),
]

_MARKER_FILES = (
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "environment.yml",
    "environment.yaml", "conda.yaml", "conda.yml", "setup.py", "setup.cfg",
    "Pipfile", "package.json",
)
_IDEA_FILES = ("EXPERIMENT.md", "IDEA.md", "README.md", "README.zh-CN.md", "README.en.md")

_ENV_RE = re.compile(r"[\\/]envs?[\\/]([^\\/]+)", re.IGNORECASE)


class DescriptionStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.entries: dict[str, dict] = {}
        self._folder_meta_cache: dict[str, tuple[float, dict]] = {}
        self._framework_cache: dict[str, tuple[float, str, str]] = {}
        self._load()

    # -- 持久化 ---------------------------------------------------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    self.entries = {
                        str(key): value for key, value in data.items()
                        if isinstance(value, dict)
                    }
        except Exception:
            self.entries = {}

    def save(self) -> None:
        try:
            with self._lock:
                payload = json.dumps(self.entries, ensure_ascii=False, indent=2)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:
            log_line(f"descriptions save failed: {exc}")

    # -- 读写 -----------------------------------------------------------
    def get(self, fingerprint: str) -> Optional[dict]:
        with self._lock:
            entry = self.entries.get(fingerprint)
            return dict(entry) if entry else None

    def set(self, fingerprint: str, idea: str, framework: str, note: str) -> None:
        with self._lock:
            self.entries[fingerprint] = {
                "idea": (idea or "").strip(),
                "framework": (framework or "").strip(),
                "note": (note or "").strip(),
                "updated": time.time(),
            }
        self.save()

    def clear(self, fingerprint: str) -> None:
        with self._lock:
            self.entries.pop(fingerprint, None)
        self.save()

    # -- 自动识别 -------------------------------------------------------
    def detect_framework(self, folder: str, cmdline: str, exe: str) -> tuple[str, str]:
        """返回 (框架名, 依据说明)。"""
        folder = (folder or "").strip()
        now = time.monotonic()
        cached = self._framework_cache.get(folder.lower()) if folder else None
        if cached and now - cached[0] < 120:
            return cached[1], cached[2]

        text_parts: list[str] = [cmdline or "", exe or "", folder or ""]
        env_match = _ENV_RE.search(exe or "")
        if env_match:
            text_parts.append(env_match.group(1))
        if folder and os.path.isdir(folder):
            text_parts.extend(self._read_marker_files(folder))
        text = "\n".join(part for part in text_parts if part).lower()

        matched: list[str] = []
        for name, keywords in _FRAMEWORK_RULES:
            if any(keyword in text for keyword in keywords):
                matched.append(name)
        if matched:
            framework = " + ".join(dict.fromkeys(matched[:2]))
            source = "依赖文件/环境"
        else:
            framework, source = self._fallback_framework(exe, cmdline)
        if folder:
            self._framework_cache[folder.lower()] = (now, framework, source)
        return framework, source

    @staticmethod
    def _fallback_framework(exe: str, cmdline: str) -> tuple[str, str]:
        base = basename(exe).lower()
        command = (cmdline or "").lower()
        if "python" in base or "python" in command:
            return "Python", "宿主程序"
        if "node" in base:
            return "Node.js", "宿主程序"
        if "julia" in base:
            return "Julia", "宿主程序"
        if "matlab" in base:
            return "MATLAB", "宿主程序"
        if base in ("rscript.exe", "r.exe"):
            return "R", "宿主程序"
        if "java" in base:
            return "Java", "宿主程序"
        return (basename(exe) or "未知"), "宿主程序"

    @staticmethod
    def _read_marker_files(folder: str) -> list[str]:
        chunks: list[str] = []
        for name in _MARKER_FILES:
            path = os.path.join(folder, name)
            try:
                if os.path.isfile(path) and os.path.getsize(path) <= 256 * 1024:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        chunks.append(handle.read(64 * 1024))
            except Exception:
                continue
        return chunks

    def folder_meta(self, folder: str) -> dict:
        """读取文件夹中的 .experiment.json / EXPERIMENT.md 等自动描述。"""
        folder = (folder or "").strip()
        if not folder or not os.path.isdir(folder):
            return {}
        now = time.monotonic()
        cached = self._folder_meta_cache.get(folder.lower())
        if cached and now - cached[0] < 60:
            return cached[1]

        meta: dict = {}
        json_path = os.path.join(folder, ".experiment.json")
        if not os.path.isfile(json_path):
            json_path = os.path.join(folder, "experiment.json")
        if os.path.isfile(json_path) and os.path.getsize(json_path) <= 64 * 1024:
            try:
                data = json.loads(Path(json_path).read_text(encoding="utf-8-sig", errors="replace"))
                if isinstance(data, dict):
                    for key in ("idea", "framework", "note", "title", "description"):
                        value = data.get(key)
                        if isinstance(value, str) and value.strip():
                            meta[key] = value.strip()
            except Exception:
                pass
        if not meta.get("idea"):
            for name in _IDEA_FILES:
                path = os.path.join(folder, name)
                idea = self._first_line_idea(path)
                if idea:
                    meta.setdefault("idea", idea)
                    break
        self._folder_meta_cache[folder.lower()] = (now, meta)
        if len(self._folder_meta_cache) > 256:
            self._folder_meta_cache.clear()
        return meta

    @staticmethod
    def _first_line_idea(path: str) -> str:
        try:
            if not os.path.isfile(path) or os.path.getsize(path) > 512 * 1024:
                return ""
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    line = re.sub(r"^#+\s*", "", line).strip()
                    line = re.sub(r"^[-*]\s*", "", line).strip()
                    if not line or line.lower() in ("readme", "实验", "实验记录"):
                        continue
                    if line.startswith("```") or line.startswith("|"):
                        continue
                    return line[:80]
        except Exception:
            return ""
        return ""

    # -- 综合 -----------------------------------------------------------
    def resolve(self, fingerprint: str, folder: str, cmdline: str,
                exe: str) -> dict:
        entry = self.get(fingerprint) or {}
        meta = self.folder_meta(folder)
        auto_framework, framework_source = self.detect_framework(folder, cmdline, exe)
        manual = bool(entry)

        framework = entry.get("framework") or meta.get("framework") or auto_framework
        idea = entry.get("idea") if manual else (meta.get("idea") or "")
        note = entry.get("note") if manual else (meta.get("note") or "")
        if framework == auto_framework and not entry.get("framework"):
            source = "自动识别"
        elif meta.get("framework") and framework == meta.get("framework"):
            source = "项目文件"
        else:
            source = "自定义"
        return {
            "framework": framework or "",
            "framework_source": framework_source,
            "idea": idea or "",
            "note": note or "",
            "description_source": source,
            "manual": manual,
        }
