"""暂停 / 继续 / 重启恢复的单元测试（不真的挂起进程）。"""

from __future__ import annotations

import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from experiment_widget import winproc
from experiment_widget.config import Config
from experiment_widget.estimate import Tracker
from experiment_widget.history import HistoryStore
from experiment_widget.models import GpuSnapshot, ProcessStat
from experiment_widget.pauses import PauseRegistry


class PauseBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.cfg = Config(self.base / "config.json")
        self.cfg.set("detect.startup_grace_ticks", 1)
        self.cfg.set("estimate.progress_enabled", False)
        self.cfg.set("detect.idle_hide_minutes", 0)
        self.history = HistoryStore(self.base / "history.json")
        self.base_time = time.time() - 100
        self.now = self.base_time + 10
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(winproc, "cpu_count", return_value=8))
        self.stack.enter_context(mock.patch.object(winproc, "system_cpu_pct", return_value=42.0))
        self.stack.enter_context(mock.patch.object(winproc, "memory_info",
                                                   return_value=(8000.0, 16000.0)))
        self.stack.enter_context(mock.patch.object(winproc, "current_session_id", return_value=1))
        self.stack.enter_context(mock.patch.object(
            winproc, "get_command_line",
            side_effect=lambda pid, ct=0.0: '"C:\\Python\\python.exe" train.py'))
        self.stack.enter_context(mock.patch.object(
            winproc, "get_cwd", side_effect=lambda pid, ct=0.0, ttl=300.0: r"D:\proj\exp"))
        self.suspend_mock = self.stack.enter_context(mock.patch.object(
            winproc, "suspend_processes",
            side_effect=lambda pids: ([pid for pid in pids if pid != 999], [])))
        self.resume_mock = self.stack.enter_context(mock.patch.object(
            winproc, "resume_processes", side_effect=lambda pids: (list(pids), [])))
        self.tracker = self.make_tracker()

    def make_tracker(self) -> Tracker:
        return Tracker(self.cfg, HistoryStore(self.base / "history.json"))

    def proc(self, pid, ppid, cpu, create=None):
        return ProcessStat(
            pid=pid, ppid=ppid, name="python.exe", exe=r"C:\Python\python.exe",
            create_time=create if create is not None else self.base_time,
            cpu_time=cpu, mem_rss=100 * 1024 * 1024, threads=4, session_id=1)

    def tick(self, procs, dt=1.0):
        self.now += dt
        tracker = getattr(self, "tracker")
        return tracker.update({p.pid: p for p in procs}, GpuSnapshot(), self.now)

    def detect(self):
        root = self.proc(100, 1, 10.0)
        self.tick([root], 1.0)
        root.cpu_time += 4.0
        snapshot = self.tick([root], 1.0)
        self.assertEqual(snapshot.total_experiments, 1)
        return root, snapshot.folders[0].experiments[0]


class PauseTest(PauseBase):
    def test_pause_and_resume(self):
        root, exp = self.detect()
        result = self.tracker.pause(exp.key, now=self.now)
        self.assertTrue(result["ok"])
        self.suspend_mock.assert_called_once()
        session = self.tracker.sessions[exp.key]
        self.assertTrue(session.paused)
        self.assertEqual(self.tracker.pauses.count, 1)
        view = self.tracker._build_views(self.now)[0][0].experiments[0]
        self.assertTrue(view.paused)

        # 暂停期间运行时长冻结
        elapsed_before = view.elapsed
        self.now += 30
        views = self.tracker._build_views(self.now)[0]
        frozen = views[0].experiments[0]
        self.assertAlmostEqual(frozen.elapsed, elapsed_before, places=1)
        self.assertGreater(frozen.paused_seconds, 20)

        result = self.tracker.resume(exp.key, now=self.now)
        self.assertTrue(result["ok"])
        self.resume_mock.assert_called_once()
        self.assertFalse(self.tracker.sessions[exp.key].paused)
        self.assertEqual(self.tracker.pauses.count, 0)
        resumed_view = self.tracker._build_views(self.now)[0][0].experiments[0]
        self.assertFalse(resumed_view.paused)

    def test_pause_failure_rolls_back(self):
        root, exp = self.detect()
        self.suspend_mock.side_effect = lambda pids: ([], list(pids))
        result = self.tracker.pause(exp.key, now=self.now)
        self.assertFalse(result["ok"])
        self.assertFalse(self.tracker.sessions[exp.key].paused)
        self.assertEqual(self.tracker.pauses.count, 0)

    def test_restore_paused_after_restart(self):
        root, exp = self.detect()
        self.assertTrue(self.tracker.pause(exp.key, now=self.now)["ok"])
        registry = PauseRegistry(self.base / "paused.json")
        self.assertEqual(registry.count, 1)

        # 模拟小组件重启：新 Tracker 从 paused.json 恢复
        self.tracker = self.make_tracker()
        self.tick([root], 0.5)
        sessions = list(self.tracker.sessions.values())
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0].paused)
        self.assertEqual(sessions[0].title, "train.py")
        resumed = self.tracker.resume(sessions[0].key, now=self.now)
        self.assertTrue(resumed["ok"])

    def test_pause_registry_persists(self):
        root, exp = self.detect()
        self.tracker.pause(exp.key, now=self.now)
        registry = PauseRegistry(self.base / "paused.json")
        entry = registry.all()[0]
        self.assertEqual(entry["pid"], 100)
        self.assertEqual(entry["title"], "train.py")
        self.assertIn("paused_at", entry)


if __name__ == "__main__":
    unittest.main()
