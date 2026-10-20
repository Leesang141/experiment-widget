"""诊断：为什么当前实验没有剩余时间估算。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiment_widget import progress, winproc  # noqa: E402
from experiment_widget.config import Config  # noqa: E402
from experiment_widget.estimate import Tracker  # noqa: E402
from experiment_widget.history import HistoryStore  # noqa: E402
from experiment_widget.models import GpuSnapshot  # noqa: E402


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    config = Config(base / "config.json")
    config.set("estimate.progress_enabled", True)
    tracker = Tracker(config, HistoryStore(base / "history.json"))
    session_id = winproc.current_session_id()
    print("采样中（约 10 秒）…")
    for _ in range(9):
        time.sleep(1.0)
        procs = winproc.snapshot_processes(only_session=session_id)
        tracker.update(procs, GpuSnapshot(), time.time())

    for session in sorted(tracker.sessions.values(), key=lambda s: s.started_at):
        print("\n" + "=" * 78)
        print(f"{session.title}   pid={session.root_pid}")
        print(f"  folder={session.folder}")
        print(f"  cmdline={session.cmdline[:220]}")
        print(f"  elapsed={session.effective_elapsed(time.time()):.0f}s  "
              f"cpu={session.cpu_pct:.1f}%  gpu={session.gpu_util}%")
        log_path = session.miner.state.path if session.miner else None
        print(f"  log={log_path}")
        if session.miner:
            state = session.miner.state
            print(f"  miner attempts={state.discover_attempts} rejected={state.rejected} "
                  f"direct={state.direct} samples={len(state.samples)}")
        print(f"  progress={session.progress}")
        est = session.estimate
        print(f"  estimate basis={est.basis if est else None} seconds={est.seconds if est else None}")
        print(f"  detail={est.detail if est else ''}")
        if log_path and os.path.isfile(log_path):
            try:
                tail = progress.read_tail(log_path, 4096)
                lines = [ln for ln in tail.splitlines() if ln.strip()][-3:]
                print("  last lines:")
                for line in lines:
                    print("   ", line[:200])
                parsed = progress.parse_tail_text(tail)
                print("  parsed:", parsed)
            except Exception as exc:
                print("  read log error:", exc)
    tmp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
