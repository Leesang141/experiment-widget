"""联调：真实进程的暂停/继续与重启后恢复暂停状态。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiment_widget import winproc  # noqa: E402
from experiment_widget.config import Config  # noqa: E402
from experiment_widget.estimate import Tracker  # noqa: E402
from experiment_widget.history import HistoryStore  # noqa: E402
from experiment_widget.models import GpuSnapshot  # noqa: E402

HERE = Path(__file__).resolve().parent
DEMO_DIR = HERE / "demo_workspace" / "proj_alpha"
DEMO_SCRIPT = DEMO_DIR / "train_demo.py"


def make_tracker(base: Path) -> Tracker:
    config = Config(base / "config.json")
    config.set("detect.startup_grace_ticks", 1)
    history = HistoryStore(base / "history.json")
    return Tracker(config, history)


def tick(tracker: Tracker) -> None:
    procs = winproc.snapshot_processes(only_session=winproc.current_session_id())
    tracker.update(procs, GpuSnapshot(), time.time())


def find_demo(tracker: Tracker):
    for session in tracker.sessions.values():
        if session.title == "train_demo.py":
            return session
    return None


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        tracker = make_tracker(base)
        child = subprocess.Popen(
            [sys.executable, str(DEMO_SCRIPT), "--epochs", "40", "--seconds", "1.0",
             "--workers", "1"],
            cwd=str(DEMO_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        passed = True
        try:
            session = None
            for _ in range(8):
                time.sleep(1.0)
                tick(tracker)
                session = find_demo(tracker)
                if session:
                    break
            if not session:
                print("not detected")
                return 1
            print("detected:", session.title, "members:", session.members)
            result = tracker.pause(session.key)
            print("pause:", result)
            if not result.get("ok"):
                passed = False
            paused_session = tracker.sessions[session.key]
            print("paused flag:", paused_session.paused)
            # 暂停后 CPU 时间不应该再增长
            cpu_before = sum(
                winproc.snapshot_processes(only_session=winproc.current_session_id())[pid].cpu_time
                for pid in paused_session.members
                if pid in winproc.snapshot_processes(only_session=winproc.current_session_id()))
            time.sleep(1.5)
            tick(tracker)
            procs = winproc.snapshot_processes(only_session=winproc.current_session_id())
            cpu_after = sum(procs[pid].cpu_time for pid in paused_session.members if pid in procs)
            print("cpu delta while paused:", round(cpu_after - cpu_before, 3))
            if cpu_after - cpu_before > 0.3:
                passed = False

            # 模拟小组件重启：新的 Tracker 读取 paused.json，应恢复暂停中的实验
            tracker2 = make_tracker(base)
            for _ in range(3):
                time.sleep(1.0)
                tick(tracker2)
            restored = find_demo(tracker2)
            print("restored after restart:", bool(restored),
                  "paused:", restored.paused if restored else None)
            if not restored or not restored.paused:
                passed = False
            if restored:
                resume = tracker2.resume(restored.key)
                print("resume:", resume)
                if not resume.get("ok"):
                    passed = False
                time.sleep(1.2)
                procs = winproc.snapshot_processes(only_session=winproc.current_session_id())
                session2 = tracker2.sessions[restored.key]
                cpu_a = sum(procs[pid].cpu_time for pid in session2.members if pid in procs)
                time.sleep(1.2)
                tick(tracker2)
                procs = winproc.snapshot_processes(only_session=winproc.current_session_id())
                cpu_b = sum(procs[pid].cpu_time for pid in session2.members if pid in procs)
                print("cpu delta after resume:", round(cpu_b - cpu_a, 3))
                if cpu_b - cpu_a < 0.5:
                    passed = False
        finally:
            try:
                child.kill()
            except Exception:
                pass
            # 清理可能残留的 worker
            procs = winproc.snapshot_processes(only_session=winproc.current_session_id())
            for pid in procs:
                try:
                    cmd = winproc.get_command_line(pid) or ""
                except Exception:
                    cmd = ""
                if "end = time.time()" in cmd and "train_demo" not in cmd:
                    try:
                        winproc.terminate_process_tree(pid, procs=procs)
                    except Exception:
                        pass
        print("RESULT:", "PASS" if passed else "FAIL")
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
