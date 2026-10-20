"""实验识别 / 聚合 / ETA 估算测试（用 mock 去掉真实系统依赖）。"""

from __future__ import annotations

import contextlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from experiment_widget import winproc
from experiment_widget.config import Config
from experiment_widget.estimate import (
    Tracker,
    _Session,
    folder_display_name,
    make_fingerprint,
    resolve_folder,
)
from experiment_widget.history import HistoryStore, HistoryStats
from experiment_widget.models import (
    GpuDevice,
    GpuSnapshot,
    ProcessStat,
    ProgressView,
)


class FakeSystem:
    def __init__(self):
        self.commands = {
            100: '"C:\\Python312\\python.exe" train.py --epochs 3',
            101: '"C:\\Python312\\python.exe" -c "worker()"',
            102: '"C:\\Python312\\python.exe" train.py --epochs 3',
            200: r"C:\bench\mycuda.exe --iters 10000",
            201: r"C:\bench\idle.exe",
            300: r"C:\Program Files\Google\Chrome\chrome.exe",
        }
        self.cwds = {
            100: r"D:\proj\exp",
            101: r"D:\proj\exp",
            102: r"D:\proj\exp",
            200: r"D:\bench\cuda",
            201: r"D:\bench\idle",
            300: r"C:\Program Files\Google\Chrome",
        }

    def command_line(self, pid, create_time=0.0):
        return self.commands.get(pid)

    def cwd(self, pid, create_time=0.0, ttl=300.0):
        return self.cwds.get(pid)


class TrackerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base_time = time.time() - 100
        self.cfg = Config(Path(self.tmp.name) / "config.json")
        self.cfg.set("detect.startup_grace_ticks", 1)
        self.cfg.set("estimate.progress_enabled", False)
        self.cfg.set("estimate.history_min_seconds", 0)
        self.cfg.set("detect.idle_hide_minutes", 0)
        self.history = HistoryStore(Path(self.tmp.name) / "history.json")
        self.fake = FakeSystem()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(winproc, "cpu_count", return_value=8))
        self.stack.enter_context(mock.patch.object(winproc, "system_cpu_pct", return_value=42.0))
        self.stack.enter_context(mock.patch.object(winproc, "memory_info",
                                                   return_value=(8000.0, 16000.0)))
        self.stack.enter_context(mock.patch.object(winproc, "current_session_id",
                                                   return_value=1))
        self.stack.enter_context(mock.patch.object(winproc, "get_command_line",
                                                   side_effect=self.fake.command_line))
        self.stack.enter_context(mock.patch.object(winproc, "get_cwd",
                                                   side_effect=self.fake.cwd))
        self.tracker = Tracker(self.cfg, self.history)
        self.now = self.base_time + 10

    def proc(self, pid, ppid, cpu=0.0, name="python.exe",
             exe=r"C:\Python312\python.exe", create=None):
        return ProcessStat(
            pid=pid, ppid=ppid, name=name, exe=exe,
            create_time=create if create is not None else self.base_time,
            cpu_time=cpu, mem_rss=100 * 1024 * 1024,
            threads=4, session_id=1,
        )

    def tick(self, procs, dt=1.0, gpu=None):
        self.now += dt
        snapshot = self.tracker.update({p.pid: p for p in procs},
                                       gpu or GpuSnapshot(), self.now)
        return snapshot

    def gpu_snapshot(self, util=None, mem=None):
        util = util or {}
        mem = mem or {}
        return GpuSnapshot(
            devices=[GpuDevice(0, "Fake GPU", util_pct=30.0, mem_used_mb=1000,
                               mem_total_mb=8000)],
            per_pid_util=dict(util), per_pid_mem_mb=dict(mem),
            util_source={pid: "pmon" for pid in util},
            nvidia_ok=True, windows_ok=True, pmon_ok=True,
        )


class DetectionTest(TrackerTestBase):
    def test_detect_and_aggregate_children(self):
        root = self.proc(100, 1, cpu=10.0)
        child = self.proc(101, 100, cpu=5.0)
        self.tick([root, child], 1.0)  # 第一次采样建立 CPU 基准
        root.cpu_time += 4.0  # 4 秒 CPU / 1 秒 / 8 核 = 50%
        child.cpu_time += 2.0  # 2 秒 CPU / 1 秒 / 8 核 = 25%
        snapshot = self.tick([root, child], 1.0)
        self.assertEqual(snapshot.total_experiments, 1)
        self.assertEqual(len(snapshot.folders), 1)
        folder = snapshot.folders[0]
        self.assertEqual(folder.folder, r"D:\proj\exp")
        exp = folder.experiments[0]
        self.assertEqual(exp.name, "train.py")
        self.assertEqual(sorted(exp.pids), [100, 101])
        self.assertAlmostEqual(exp.cpu_pct, 6.0 / 1.0 / 8 * 100, places=2)
        self.assertGreater(exp.elapsed, 10)

    def test_idle_low_usage_not_detected(self):
        proc = self.proc(300, 1, cpu=100.0, name="chrome.exe",
                         exe=r"C:\Program Files\Google\Chrome\chrome.exe")
        self.tick([proc], 1.0)
        proc.cpu_time += 0.4  # 5% of one core on 8 cores = 0.6%，低于阈值
        snapshot = self.tick([proc], 1.0)
        self.assertEqual(snapshot.total_experiments, 0)

    def test_unknown_exe_requires_strong_gpu_signal(self):
        proc_strong = self.proc(200, 1, cpu=0.0, name="mycuda.exe",
                                exe=r"C:\bench\mycuda.exe")
        proc_weak = self.proc(201, 1, cpu=0.0, name="idle.exe",
                              exe=r"C:\bench\idle.exe")
        snapshot = self.tick([proc_strong, proc_weak], 1.0,
                             gpu=self.gpu_snapshot(util={200: 25.0}, mem={200: 700.0}))
        self.assertEqual(snapshot.total_experiments, 1)
        self.assertEqual(snapshot.folders[0].experiments[0].name, "mycuda.exe")

    def test_display_folder_name(self):
        self.assertEqual(folder_display_name(r"D:\CVPR\实验"), "CVPR\\实验")
        self.assertEqual(folder_display_name(r"D:\CVPR"), "CVPR")
        self.assertEqual(folder_display_name(r"D:\CVPR\实验\scripts\06"), "scripts\\06")
        self.assertEqual(folder_display_name(""), "未知文件夹")

    def test_resolve_folder_from_cwd(self):
        folder, source = resolve_folder(r"D:\proj\exp\\", "python train.py", "")
        self.assertEqual(folder, r"D:\proj\exp")
        self.assertEqual(source, "工作目录")


class EstimateTest(TrackerTestBase):
    def _detect_one(self):
        root = self.proc(100, 1, cpu=10.0)
        self.tick([root], 1.0)
        root.cpu_time += 4.0
        snapshot = self.tick([root], 1.0)
        self.assertEqual(snapshot.total_experiments, 1)
        return root, snapshot.folders[0].experiments[0]

    def test_manual_estimate(self):
        fingerprint = make_fingerprint(r"D:\proj\exp",
                                       self.fake.commands[100], r"C:\Python312\python.exe")
        self.cfg.data.setdefault("manual_estimates", {})[fingerprint] = 1000.0
        root, exp = self._detect_one()
        self.assertEqual(exp.estimate.basis, "manual")
        self.assertAlmostEqual(exp.estimate.seconds, 1000.0 - exp.elapsed, places=1)

    def test_history_estimate(self):
        fingerprint = make_fingerprint(r"D:\proj\exp",
                                       self.fake.commands[100], r"C:\Python312\python.exe")
        for duration in (3600.0, 3000.0):
            self.history.add(fingerprint, "train.py", r"D:\proj\exp",
                             self.fake.commands[100], duration,
                             self.now - duration, self.now)
        root, exp = self._detect_one()
        self.assertEqual(exp.estimate.basis, "history")
        self.assertAlmostEqual(exp.estimate.seconds, 3300.0 - exp.elapsed, places=1)
        self.assertEqual(exp.estimate.confidence, "medium")

    def test_history_recorded_after_process_exit(self):
        root, exp = self._detect_one()
        fingerprint = exp.fingerprint
        self.tick([], 2.0)  # root 消失
        self.assertEqual(self.history.count, 1)
        self.assertGreater(self.history.durations(fingerprint)[0], 10)

    def test_killed_process_not_recorded(self):
        root, exp = self._detect_one()
        self.tracker.sessions[exp.key].kill_requested = True
        self.tick([], 2.0)
        self.assertEqual(self.history.count, 0)

    def test_unknown_estimate_without_data(self):
        root, exp = self._detect_one()
        self.assertEqual(exp.estimate.basis, "none")
        self.assertIsNone(exp.estimate.seconds)

    def test_progress_since_start_fallback(self):
        session = _Session(
            key="k", root_pid=100, create_time=self.now - 1000,
            started_at=self.now - 1000, cmdline="python train.py",
            exe="python.exe", name="python.exe", title="train.py",
            folder=r"D:\proj\exp", folder_source="工作目录",
            fingerprint="fp", ancestor_cmdlines=[], miner=None,
            last_active=self.now,
        )
        session.progress = ProgressView(
            pct=20.0, label="200/1000", current=200, total=1000,
            eta_seconds=None, rate=None, source="日志 · 计数", kind="fraction")
        estimate = self.tracker._estimate(
            session, self.now, None, HistoryStats(0, None, None, None, None))
        self.assertEqual(estimate.basis, "progress")
        self.assertEqual(estimate.confidence, "low")
        self.assertAlmostEqual(estimate.seconds, 4000.0, places=1)

    def test_hidden_when_idle(self):
        self.cfg.set("detect.idle_hide_minutes", 0.01)  # 0.6 秒
        root, exp = self._detect_one()
        snapshot = self.tick([root], 1.5)
        self.assertEqual(snapshot.total_experiments, 0)
        self.assertEqual(snapshot.hidden_experiments, 1)


if __name__ == "__main__":
    unittest.main()
