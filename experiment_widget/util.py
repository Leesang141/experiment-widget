"""通用小工具：子进程、日志、字符串处理。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

CREATE_NO_WINDOW = 0x08000000

_lock = threading.Lock()
_debug_enabled: Optional[bool] = None
_log_path: Optional[Path] = None


def debug_enabled() -> bool:
    global _debug_enabled
    if _debug_enabled is None:
        _debug_enabled = bool(os.environ.get("EXPERIMENT_WIDGET_DEBUG")) or "--debug" in sys.argv
    return _debug_enabled


def log_line(message: str) -> None:
    """调试日志（设置环境变量 EXPERIMENT_WIDGET_DEBUG=1 或 --debug 后启用）。"""
    if not debug_enabled():
        return
    global _log_path
    try:
        if _log_path is None:
            from .config import DATA_DIR

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _log_path = DATA_DIR / "widget.log"
        with _lock:
            if _log_path.exists() and _log_path.stat().st_size > 2 * 1024 * 1024:
                _log_path.write_text("", encoding="utf-8")
            with _log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


def run_process(args: list[str], timeout: float = 8.0) -> Optional[str]:
    """运行子进程并返回 stdout（UTF-8 解码）。不会弹出控制台窗口。"""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0 and not out.strip():
            log_line(f"run_process {args[0]} rc={proc.returncode} err={err.strip()[:300]}")
        return out if out or proc.returncode == 0 else None
    except subprocess.TimeoutExpired:
        log_line(f"run_process timeout: {args}")
        return None
    except Exception as exc:
        log_line(f"run_process failed: {exc}")
        return None


def powershell_path() -> Optional[str]:
    import shutil

    for name in ("powershell.exe", "pwsh.exe"):
        path = shutil.which(name)
        if path:
            return path
    for candidate in (
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


_powershell_cache: Optional[str] = None
_powershell_checked = False


def run_powershell(script: str, timeout: float = 8.0) -> Optional[str]:
    global _powershell_cache, _powershell_checked
    if not _powershell_checked:
        _powershell_cache = powershell_path()
        _powershell_checked = True
    if not _powershell_cache:
        return None
    encoded = (
        "$ErrorActionPreference='SilentlyContinue';"
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        + script
    )
    return run_process(
        [_powershell_cache, "-NoProfile", "-NonInteractive", "-Command", encoded],
        timeout=timeout,
    )


def basename(path: str) -> str:
    if not path:
        return ""
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def dirname(path: str) -> str:
    if not path:
        return ""
    path = path.rstrip("\\/")
    idx = max(path.rfind("\\"), path.rfind("/"))
    if idx <= 0:
        return path
    return path[:idx]


def strip_quotes(text: str) -> str:
    text = (text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def normalize_folder(path: str) -> str:
    """统一文件夹写法用于比较（大小写、斜杠、尾部空格）。"""
    if not path:
        return ""
    path = path.strip().strip('"').rstrip("\\/")
    path = path.replace("/", "\\")
    return path.lower()


def tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", (text or "").lower()) if t}


def similarity(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    return overlap / max(1, min(len(ta), len(tb)))


def truncate_text(text: str, limit: int = 200) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"
