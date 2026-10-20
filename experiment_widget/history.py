"""实验运行历史：记录已观察到的完整运行时长，用于下一次同类实验的剩余时间估计。"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path
from typing import Optional


class HistoryStore:
    def __init__(self, path: Path, max_records: int = 500):
        self.path = Path(path)
        self.max_records = max(20, int(max_records))
        self.records: list[dict] = []
        self.revision = 0
        self._load()

    # -- 读写 -----------------------------------------------------------
    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if isinstance(data, list):
                    self.records = [r for r in data if self._valid(r)]
        except Exception:
            self.records = []
        self._sort()

    def _valid(self, record) -> bool:
        return (
            isinstance(record, dict)
            and isinstance(record.get("fp"), str)
            and isinstance(record.get("seconds"), (int, float))
            and record["seconds"] > 0
        )

    def _sort(self) -> None:
        self.records.sort(key=lambda r: r.get("ended", 0))

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.records, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass

    # -- 记录 -----------------------------------------------------------
    def add(self, fingerprint: str, label: str, folder: str, cmdline: str,
            seconds: float, started_at: float, ended_at: Optional[float] = None) -> bool:
        if not fingerprint or seconds <= 0:
            return False
        ended_at = ended_at or time.time()
        # 防止同一实验在短暂窗口内重复记录
        for record in reversed(self.records[-6:]):
            if (record.get("fp") == fingerprint
                    and abs(float(record.get("ended", 0)) - ended_at) < 120
                    and abs(float(record.get("seconds", 0)) - seconds) < 2):
                return False
        self.records.append({
            "fp": fingerprint,
            "label": label,
            "folder": folder,
            "cmd": (cmdline or "")[:1000],
            "seconds": round(float(seconds), 2),
            "started": round(float(started_at), 2),
            "ended": round(float(ended_at), 2),
        })
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]
        self.revision += 1
        self.save()
        return True

    def clear(self) -> None:
        self.records = []
        self.revision += 1
        self.save()

    @property
    def count(self) -> int:
        return len(self.records)

    # -- 查询 -----------------------------------------------------------
    def durations(self, fingerprint: str, limit: int = 12) -> list[float]:
        values = [float(r["seconds"]) for r in self.records if r.get("fp") == fingerprint]
        return values[-limit:]

    def stats(self, fingerprint: str) -> "HistoryStats":
        values = self.durations(fingerprint)
        if not values:
            return HistoryStats(0, None, None, None, None)
        return HistoryStats(
            count=len(values),
            median=statistics.median(values),
            last=values[-1],
            min_value=min(values),
            max_value=max(values),
        )

    def recent(self, limit: int = 5) -> list[dict]:
        return list(reversed(self.records[-limit:]))


class HistoryStats:
    __slots__ = ("count", "median", "last", "min", "max")

    def __init__(self, count: int, median: Optional[float], last: Optional[float],
                 min_value: Optional[float], max_value: Optional[float]):
        self.count = count
        self.median = median
        self.last = last
        self.min = min_value
        self.max = max_value
