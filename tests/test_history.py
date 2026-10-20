"""历史记录与格式化测试。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from experiment_widget import fmt
from experiment_widget.history import HistoryStore


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.json"
        self.store = HistoryStore(self.path, max_records=20)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stats_median(self):
        now = time.time()
        for duration in (100, 200, 300):
            self.store.add("fp|train.py", "train.py", "D:/p", "python train.py",
                           duration, now - duration, now)
        stats = self.store.stats("fp|train.py")
        self.assertEqual(stats.count, 3)
        self.assertEqual(stats.median, 200)
        self.assertEqual(stats.last, 300)
        self.assertEqual(stats.min, 100)
        self.assertEqual(stats.max, 300)
        self.assertEqual(self.store.stats("unknown").count, 0)

    def test_persist_and_reload(self):
        now = time.time()
        self.store.add("fp", "label", "D:/p", "cmd", 80, now - 80, now)
        self.store.save()
        reloaded = HistoryStore(self.path, max_records=20)
        self.assertEqual(reloaded.count, 1)
        self.assertEqual(reloaded.stats("fp").median, 80)

    def test_max_records(self):
        now = time.time()
        for index in range(40):
            self.store.add(f"fp{index}", "l", "D:/p", "cmd", 60 + index, now, now)
        self.assertLessEqual(self.store.count, 20)
        self.assertEqual(self.store.stats("fp0").count, 0)

    def test_duplicate_guard(self):
        now = time.time()
        self.store.add("fp", "l", "D:/p", "cmd", 60, now - 60, now)
        self.store.add("fp", "l", "D:/p", "cmd", 60, now - 60, now)
        self.assertEqual(self.store.count, 1)

    def test_clear(self):
        now = time.time()
        self.store.add("fp", "l", "D:/p", "cmd", 60, now, now)
        self.store.clear()
        self.assertEqual(self.store.count, 0)


class FormatTest(unittest.TestCase):
    def test_elapsed(self):
        self.assertEqual(fmt.fmt_elapsed(0), "0:00")
        self.assertEqual(fmt.fmt_elapsed(75), "1:15")
        self.assertEqual(fmt.fmt_elapsed(3723), "1:02:03")
        self.assertEqual(fmt.fmt_elapsed(90000), "1天 01:00")

    def test_eta(self):
        self.assertEqual(fmt.fmt_eta(None), "未知")
        self.assertEqual(fmt.fmt_eta(5), "即将完成")
        self.assertEqual(fmt.fmt_eta(45), "≈ 45 秒")
        self.assertEqual(fmt.fmt_eta(600), "≈ 10 分")
        self.assertEqual(fmt.fmt_eta(3600), "≈ 1 小时")
        self.assertEqual(fmt.fmt_eta(3900), "≈ 1 小时 5 分")
        self.assertEqual(fmt.fmt_eta(86400 * 2.5), "≈ 2.5 天")

    def test_mb(self):
        self.assertEqual(fmt.fmt_mb(None), "—")
        self.assertEqual(fmt.fmt_mb(173), "173 MB")
        self.assertEqual(fmt.fmt_mb(1536), "1.50 GB")

    def test_parse_duration(self):
        self.assertEqual(fmt.parse_duration_text("90"), 90 * 60)
        self.assertEqual(fmt.parse_duration_text("90m"), 90 * 60)
        self.assertEqual(fmt.parse_duration_text("1.5h"), 5400)
        self.assertEqual(fmt.parse_duration_text("2h30m"), 9000)
        self.assertEqual(fmt.parse_duration_text("1:30"), 5400)
        self.assertIsNone(fmt.parse_duration_text("abc"))
        self.assertIsNone(fmt.parse_duration_text(""))


if __name__ == "__main__":
    unittest.main()
