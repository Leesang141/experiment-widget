"""联调用的模拟训练脚本：多进程 CPU 负载 + 日志进度。

用法：
    python train_demo.py [--epochs 30] [--seconds 1.5] [--workers 2]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
LOG_PATH = os.path.join(LOG_DIR, "train.log")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--seconds", type=float, default=1.5)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--log", default=LOG_PATH)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
    worker_seconds = args.epochs * args.seconds + 30
    worker_code = (
        "import time\n"
        f"end = time.time() + {worker_seconds}\n"
        "while time.time() < end:\n"
        "    pass\n"
    )
    workers = []
    for _ in range(max(0, args.workers)):
        try:
            workers.append(subprocess.Popen([sys.executable, "-c", worker_code]))
        except Exception:
            pass
    try:
        with open(args.log, "w", encoding="utf-8", buffering=1) as log:
            log.write(f"gpu=False epochs={args.epochs}\n")
            for epoch in range(1, args.epochs + 1):
                started = time.time()
                log.write(f"Epoch {epoch}/{args.epochs} loss={1.0 / epoch:.4f}\n")
                log.flush()
                while time.time() - started < args.seconds:
                    pass
            log.write("training finished\n")
    finally:
        for worker in workers:
            try:
                worker.terminate()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
