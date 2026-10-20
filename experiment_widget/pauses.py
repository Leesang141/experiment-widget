"""暂停实验的持久化登记。

实验被“安全暂停”后会写入 ``data/paused.json``，记录 pid + 创建时间 +
指纹/文件夹/命令行等元数据。这样即使小组件重启，也能把仍处于冻结状态的
实验重新显示出来，并提供“继续”按钮。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .util import log_line


class PauseRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.entries: dict[str, dict] = {}
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
            log_line(f"paused registry save failed: {exc}")

    # -- 查询 / 修改 -----------------------------------------------------
    @staticmethod
    def make_key(pid: int, create_time: float) -> str:
        return f"{pid}@{create_time:.3f}"

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self.entries.values()]

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            entry = self.entries.get(key)
            return dict(entry) if entry else None

    def add(self, key: str, entry: dict) -> None:
        with self._lock:
            self.entries[key] = dict(entry, paused_at=time.time())
        self.save()

    def remove(self, key: str) -> None:
        with self._lock:
            existed = self.entries.pop(key, None)
        if existed is not None:
            self.save()

    def clear(self) -> None:
        with self._lock:
            self.entries.clear()
        self.save()

    def prune_dead(self, alive_keys: set[str]) -> None:
        """移除已经不存在（被外部结束）的暂停记录。"""
        with self._lock:
            dead = [key for key in self.entries if key not in alive_keys]
            for key in dead:
                self.entries.pop(key, None)
        if dead:
            self.save()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self.entries)
