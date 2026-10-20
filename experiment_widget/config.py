"""配置读写。

配置是可读的 JSON（默认在 <项目>/data/config.json），方便手动修改。
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = APP_ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "history.json"
DESCRIPTIONS_PATH = DATA_DIR / "descriptions.json"
PAUSED_PATH = DATA_DIR / "paused.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "window": {
        "x": None,
        "y": None,
        "collapsed": False,
        "opacity": 0.98,
        "always_on_top": True,
        "height": None,
    },
    "sampling": {
        "ui_interval": 2.0,  # UI 刷新间隔（秒）
        "gpu_interval": 2.5,  # GPU 采样间隔（秒）
    },
    "detect": {
        "min_cpu_pct": 2.0,  # 占整机 CPU 百分比
        "min_gpu_util": 3.0,
        "min_gpu_mem_mb": 80.0,
        "unknown_gpu_mem_mb": 512.0,
        "startup_grace_ticks": 2,
        "idle_hide_minutes": 90,  # 空闲实验隐藏但继续跟踪
        "max_visible": 14,
        "ignore_exe": [
            "system",
            "registry",
            "secure system",
            "memory compression",
            "smss.exe",
            "csrss.exe",
            "wininit.exe",
            "services.exe",
            "lsass.exe",
            "svchost.exe",
            "fontdrvhost.exe",
            "dwm.exe",
            "winlogon.exe",
            "explorer.exe",
            "spoolsv.exe",
            "searchindexer.exe",
            "searchprotocolhost.exe",
            "searchfilterhost.exe",
            "searchhost.exe",
            "msmpeng.exe",
            "nisrv.exe",
            "securityhealthservice.exe",
            "securityhealthsystray.exe",
            "ctfmon.exe",
            "taskhostw.exe",
            "runtimebroker.exe",
            "shellexperiencehost.exe",
            "startmenuexperiencehost.exe",
            "textinputhost.exe",
            "dllhost.exe",
            "conhost.exe",
            "openconsole.exe",
            "wmiprvse.exe",
            "audiodg.exe",
            "sihost.exe",
            "shellexperiencehost.exe",
            "applicationframehost.exe",
            "lockapp.exe",
            "widgetboard.exe",
            "nvidia overlay.exe",
            "nvidia share.exe",
            "nvidia app.exe",
            "nvcontainer.exe",
            "nvsphelper64.exe",
            "nahimic3.exe",
            "nahimicsvc64.exe",
            "rtkuwp.exe",
            "yourphone.exe",
            "phoneexperiencehost.exe",
            "crossdeviceresume.exe",
            "chrome.exe",
            "msedge.exe",
            "msedgewebview2.exe",
            "firefox.exe",
            "code.exe",
            "cursor.exe",
            "devenv.exe",
            "idea64.exe",
            "pycharm64.exe",
            "webstorm64.exe",
            "wechat.exe",
            "weixin.exe",
            "qq.exe",
            "wps.exe",
            "et.exe",
            "dingtalk.exe",
            "ms-teams.exe",
            "teams.exe",
            "discord.exe",
            "steam.exe",
            "steamwebhelper.exe",
            "deepseek harness.exe",
            "chatgpt.exe",
            "codex.exe",
            "todesk.exe",
            "globalprotect.exe",
            "onedrive.exe",
        ],
        "ignore_cmd_contains": [
            "pylance",
            "pyright",
            "language-server",
            "language_server",
            "claude-code",
            ".claude",
            "codex",
            "cua-repl",
            "vscode-server",
            "cursor-server",
            "experiment_widget",
            "experiment-monitor",
        ],
        "host_exes": [
            "python.exe",
            "pythonw.exe",
            "python3.exe",
            "python3.11.exe",
            "python3.12.exe",
            "python3.13.exe",
            "pypy.exe",
            "pypy3.exe",
            "ipython.exe",
            "jupyter.exe",
            "jupyter-lab.exe",
            "julia.exe",
            "matlab.exe",
            "octave.exe",
            "rscript.exe",
            "r.exe",
            "node.exe",
            "java.exe",
            "javaw.exe",
            "cargo.exe",
            "rustc.exe",
            "go.exe",
            "dotnet.exe",
            "uv.exe",
            "conda.exe",
            "mamba.exe",
            "micromamba.exe",
            "tensorboard.exe",
        ],
    },
    "estimate": {
        "history_enabled": True,
        "history_min_seconds": 45.0,
        "history_max_records": 500,
        "progress_enabled": True,
        "log_scan": True,
        "log_stale_seconds": 900.0,
        "custom_progress_rules": [],
    },
    "manual_estimates": {},
    "log_overrides": {},
    "ui": {
        "show_idle": True,
        "show_descriptions": True,
        "font_scale": 1.0,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else CONFIG_PATH
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    # -- 读取 ---------------------------------------------------------------
    def load(self) -> None:
        try:
            if self.path.exists():
                user = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(user, dict):
                    self.data = _deep_merge(DEFAULT_CONFIG, user)
        except Exception:
            # 配置损坏时退回默认值，不影响主程序
            self.data = copy.deepcopy(DEFAULT_CONFIG)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any, save: bool = False) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        if save:
            self.save()

    # -- 写入 ---------------------------------------------------------------
    def save(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass


def app_root() -> Path:
    """程序根目录（含 run_widget.py 的目录）。"""
    return APP_ROOT


def open_in_explorer(path: str | Path) -> None:
    """在资源管理器中打开文件所在目录。"""
    p = Path(path)
    try:
        if p.is_file():
            os.startfile(str(p.parent))  # type: ignore[attr-defined]
        elif p.exists():
            os.startfile(str(p))  # type: ignore[attr-defined]
    except Exception:
        if sys.platform == "win32":
            os.system(f'explorer "{p}"')
