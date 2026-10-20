"""启动入口（供 启动实验监控.vbs / pythonw 调用）。

用法：
    pythonw run_widget.py           正常启动
    python  run_widget.py --demo    演示模式
    python  run_widget.py --debug   带调试日志启动
"""

from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from experiment_widget.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
