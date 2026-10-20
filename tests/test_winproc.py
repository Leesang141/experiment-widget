"""Windows 进程探测测试（部分需要真实环境，默认跳过）。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from experiment_widget import winproc

LIVE = bool(os.environ.get("WIDGET_LIVE_TESTS"))


class FmtUtilsTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(winproc.cpu_count() >= 1, True)
        self.assertTrue(0 <= winproc.system_cpu_pct() <= 100)


@unittest.skipUnless(LIVE, "需要真实 Windows 进程环境")
class LiveProcessTest(unittest.TestCase):
    def test_self_info(self):
        procs = winproc.snapshot_processes()
        stat = procs.get(os.getpid())
        self.assertIsNotNone(stat)
        self.assertIn("python", stat.name.lower())
        cmdline = winproc.get_command_line(os.getpid(), stat.create_time)
        self.assertIsNotNone(cmdline)
        cwd = winproc.get_cwd(os.getpid(), stat.create_time)
        self.assertIsNotNone(cwd)
        self.assertTrue(winproc.is_process_alive(os.getpid(), stat.create_time))

    def test_child_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=tmp,
            )
            try:
                time.sleep(0.8)
                procs = winproc.snapshot_processes()
                stat = procs.get(child.pid)
                self.assertIsNotNone(stat)
                cwd = winproc.get_cwd(child.pid, stat.create_time)
                self.assertIsNotNone(cwd)
                self.assertEqual(os.path.normcase(os.path.abspath(cwd)),
                                 os.path.normcase(os.path.abspath(tmp)))
                cmdline = winproc.get_command_line(child.pid, stat.create_time)
                self.assertIn("time.sleep", cmdline or "")
                self.assertTrue(winproc.is_process_alive(child.pid, stat.create_time))
            finally:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except Exception:
                    pass

    def test_terminate_tree(self):
        child = subprocess.Popen(
            [sys.executable, "-c",
             "import subprocess, sys, time; "
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
             "time.sleep(60)"]
        )
        try:
            time.sleep(1.0)
            procs = winproc.snapshot_processes()
            killed = winproc.terminate_process_tree(child.pid, procs=procs)
            self.assertGreaterEqual(len(killed), 1)
        finally:
            try:
                child.kill()
            except Exception:
                pass

    def test_suspend_resume(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "while True: pass"])
        try:
            time.sleep(1.0)
            procs = winproc.snapshot_processes()
            before = procs[child.pid].cpu_time
            suspended, failed = winproc.suspend_processes([child.pid])
            self.assertEqual(suspended, [child.pid])
            self.assertEqual(failed, [])
            time.sleep(1.2)
            procs = winproc.snapshot_processes()
            frozen = procs[child.pid].cpu_time
            self.assertLess(frozen - before, 0.2)
            resumed, failed = winproc.resume_processes([child.pid])
            self.assertEqual(resumed, [child.pid])
            self.assertEqual(failed, [])
            time.sleep(1.2)
            procs = winproc.snapshot_processes()
            self.assertGreater(procs[child.pid].cpu_time - frozen, 0.5)
        finally:
            try:
                child.kill()
            except Exception:
                pass

    def test_system_metrics(self):
        winproc.system_cpu_pct()
        time.sleep(0.3)
        value = winproc.system_cpu_pct()
        self.assertTrue(0 <= value <= 100)
        used, total = winproc.memory_info()
        self.assertGreater(total, 0)
        self.assertGreaterEqual(used, 0)
        x, y, w, h = winproc.virtual_screen_rect()
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)


if __name__ == "__main__":
    unittest.main()
