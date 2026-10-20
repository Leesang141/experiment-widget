"""后台采样线程：进程快照 + GPU 采样 + 实验跟踪。"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

from . import winproc
from .config import HISTORY_PATH, Config
from .estimate import Tracker
from .gpu import GpuPoller
from .history import HistoryStore
from .models import Snapshot
from .util import log_line


class Sampler(threading.Thread):
    """独立线程做重活（枚举进程、读日志、跑 nvidia-smi），UI 线程只消费快照。"""

    def __init__(self, config: Config):
        super().__init__(name="experiment-sampler", daemon=True)
        self.config = config
        self.queue: "queue.Queue[Snapshot]" = queue.Queue(maxsize=1)
        self.history = HistoryStore(
            HISTORY_PATH,
            max_records=int(config.get("estimate.history_max_records", 500) or 500),
        )
        self.tracker = Tracker(config, self.history)
        self.gpu_poller = GpuPoller(
            interval=float(config.get("sampling.gpu_interval", 2.5) or 2.5),
            enable_pmon=True,
        )
        self.session_id = winproc.current_session_id()
        self._stop_event = threading.Event()
        self._last_snapshot: Optional[Snapshot] = None
        self._last_error = ""

    # -- 生命周期 -------------------------------------------------------
    def run(self) -> None:
        self.gpu_poller.start()
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                procs = winproc.snapshot_processes(only_session=self.session_id)
                gpu = self.gpu_poller.snapshot
                snapshot = self.tracker.update(procs, gpu, time.time())
                self._last_error = ""
                self._publish(snapshot)
            except Exception as exc:  # pragma: no cover - 防御式编程
                self._last_error = f"{type(exc).__name__}: {exc}"
                log_line(f"sampler error: {self._last_error}")
                if self._last_snapshot is not None:
                    self._last_snapshot.sampler_error = self._last_error
                    self._publish(self._last_snapshot)
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.2, 1.0 - elapsed))

    def stop(self) -> None:
        self._stop_event.set()
        self.gpu_poller.stop()

    # -- 数据发布 -------------------------------------------------------
    def _publish(self, snapshot: Snapshot) -> None:
        self._last_snapshot = snapshot
        # 只保留最新一份，避免 UI 卡顿时积压
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.queue.put_nowait(snapshot)
        except queue.Full:
            pass

    def poll_latest(self) -> Optional[Snapshot]:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None
