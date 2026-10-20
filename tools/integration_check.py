"""端到端联调：启动真实子进程实验，用 Tracker 采样，验证识别 / 聚合 / 进度 / 历史。

用法：
    python tools/integration_check.py
"""

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
from experiment_widget.gpu import GpuPoller  # noqa: E402
from experiment_widget.history import HistoryStore  # noqa: E402

HERE = Path(__file__).resolve().parent
DEMO_DIR = HERE / "demo_workspace" / "proj_alpha"
DEMO_SCRIPT = DEMO_DIR / "train_demo.py"
PYTHON = sys.executable


def launch(epochs: int, seconds: float) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, str(DEMO_SCRIPT), "--epochs", str(epochs), "--seconds", str(seconds),
         "--workers", "1"],
        cwd=str(DEMO_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    config = Config(tmp_path / "config.json")
    config.set("detect.startup_grace_ticks", 1)
    config.set("estimate.history_min_seconds", 4.0)
    history = HistoryStore(tmp_path / "history.json")
    tracker = Tracker(config, history)
    poller = GpuPoller(interval=2.5)
    poller.start()
    session_id = winproc.current_session_id()
    print("temp:", tmp_path)

    def tick() -> tuple:
        procs = winproc.snapshot_processes(only_session=session_id)
        snapshot = tracker.update(procs, poller.snapshot, time.time())
        return snapshot, procs

    def show(snapshot, label: str) -> None:
        print(f"\n--- {label} t={time.strftime('%H:%M:%S')} 实验数={snapshot.total_experiments}")
        for folder in snapshot.folders:
            for exp in folder.experiments:
                est = exp.estimate
                print(f"  {exp.name:<22} folder={exp.folder}")
                print(f"    CPU={exp.cpu_pct:5.1f}% GPU={exp.gpu_util} 显存={exp.gpu_mem_mb} "
                      f"进程={exp.pids} 已运行={exp.elapsed:.0f}s")
                if exp.progress:
                    print(f"    进度={exp.progress.pct:5.1f}% {exp.progress.label} "
                          f"rate={exp.progress.rate} stale={exp.progress.stale}")
                if est:
                    print(f"    ETA={est.seconds} basis={est.basis} conf={est.confidence} "
                          f"detail={est.detail}")
                if exp.log_path:
                    print(f"    log={exp.log_path}")

    passed = True
    # 第一轮：跑 10 秒后结束
    child = launch(epochs=10, seconds=1.0)
    first_fingerprint = None
    progress_seen = False
    history_estimate_seen = False
    try:
        for index in range(14):
            time.sleep(1.0)
            snapshot, procs = tick()
            if index == 8:
                show(snapshot, "运行中")
            for folder in snapshot.folders:
                for exp in folder.experiments:
                    if exp.name == "train_demo.py":
                        first_fingerprint = exp.fingerprint
                        if exp.progress is not None and exp.progress.pct > 0:
                            progress_seen = True
            if child.poll() is not None and index > 5:
                break
        # 进程结束后多采样一次，触发历史记录
        for _ in range(3):
            time.sleep(1.0)
            snapshot, _ = tick()
        show(snapshot, "第一轮结束后")
        print("history count:", history.count)
        if history.count == 0:
            passed = False
            print("!! 未记录历史")
    finally:
        try:
            child.kill()
        except Exception:
            pass

    # 第二轮：同样的实验，应看到历史估算（或进度估算）
    if first_fingerprint:
        child2 = launch(epochs=6, seconds=1.0)
        try:
            for index in range(12):
                time.sleep(1.0)
                snapshot, _ = tick()
                for folder in snapshot.folders:
                    for exp in folder.experiments:
                        if exp.fingerprint == first_fingerprint and exp.estimate:
                            if exp.estimate.basis in ("history", "progress"):
                                history_estimate_seen = True
                if index == 7:
                    show(snapshot, "第二轮运行中")
                if child2.poll() is not None and index > 4:
                    break
        finally:
            try:
                child2.kill()
            except Exception:
                pass

    print("\n结果:")
    print("  识别到实验:", bool(first_fingerprint))
    print("  读取到进度:", progress_seen)
    print("  基于历史/进度的估算:", history_estimate_seen)
    passed = passed and bool(first_fingerprint) and history_estimate_seen
    poller.stop()
    tmp.cleanup()
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
