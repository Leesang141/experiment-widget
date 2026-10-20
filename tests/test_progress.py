"""进度解析 / 日志发现测试。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from experiment_widget import progress
from experiment_widget.config import Config


class ParseTest(unittest.TestCase):
    def test_tqdm_with_counts(self):
        text = " 45%|████     | 450/1000 [01:23<02:34, 6.1it/s]"
        parsed = progress.parse_tail_text(text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.kind, "fraction")
        self.assertAlmostEqual(parsed.cur, 450)
        self.assertAlmostEqual(parsed.total, 1000)
        self.assertAlmostEqual(parsed.tqdm_eta, 154)
        self.assertEqual(parsed.source, "tqdm")

    def test_tqdm_mm_ss_remaining(self):
        text = " 10%|█         | 100/1000 [00:10<01:30, 9.9it/s]"
        parsed = progress.parse_tail_text(text)
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed.tqdm_eta, 90)

    def test_bare_fraction_line(self):
        parsed = progress.parse_tail_text("  500/7442  fallback_so_far=0")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.cur, 500)
        self.assertEqual(parsed.total, 7442)
        self.assertAlmostEqual(parsed.pct, 500 / 7442 * 100)

    def test_epoch_fraction(self):
        parsed = progress.parse_tail_text("Epoch 3/50 - loss: 0.123")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.source, "epoch")
        self.assertEqual((parsed.cur, parsed.total), (3, 50))

    def test_step_fraction(self):
        parsed = progress.parse_tail_text("global step: 1200/5000")
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.cur, parsed.total), (1200, 5000))

    def test_percent_bar(self):
        parsed = progress.parse_tail_text("[##########          ] 45%  Epoch 2")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.kind, "percent")
        self.assertAlmostEqual(parsed.pct, 45)

    def test_date_not_mistaken_as_progress(self):
        parsed = progress.parse_tail_text("2026/10/18 23:59:00 training started")
        self.assertIsNone(parsed)

    def test_invalid_fraction_ignored(self):
        self.assertIsNone(progress.parse_tail_text("12/5 something"))
        self.assertIsNone(progress.parse_tail_text("900/500 files"))

    def test_latest_line_wins(self):
        text = "Epoch 1/10\nEpoch 2/10\nEpoch 3/10\n"
        parsed = progress.parse_tail_text(text)
        self.assertEqual(parsed.cur, 3)

    def test_fraction_beats_percent(self):
        text = "Progress: 75%\nEpoch 3/10\n"
        parsed = progress.parse_tail_text(text)
        self.assertEqual(parsed.kind, "fraction")
        self.assertEqual(parsed.cur, 3)

    def test_custom_rule(self):
        rules = progress.compile_rules([
            {"name": "pair", "pattern": r"(?P<cur>\d+)\s+of\s+(?P<total>\d+)"},
        ])
        parsed = progress.parse_tail_text("fold 3 of 50", rules)
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.cur, parsed.total), (3, 50))
        self.assertEqual(parsed.source, "pair")
        # 只有 percent 命名组的规则
        parsed = progress.parse_tail_text("42% done", progress.compile_rules([
            {"name": "mybar", "pattern": r"(?P<pct>\d+)% done"},
        ]))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.kind, "percent")
        self.assertAlmostEqual(parsed.pct, 42)
        # 无命名组的规则不会误用
        self.assertIsNone(progress.parse_tail_text(
            "custom42", progress.compile_rules([
                {"name": "bad", "pattern": r"custom(\d+)"},
            ])))


class MinerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.config = Config(self.dir / "config.json")
        self.log = self.dir / "run.log"
        self.log.write_text("Epoch 1/10 loss=1.0\n", encoding="utf-8")
        self.miner = progress.ProgressMiner(self.config, time.time() - 100,
                                            override=str(self.log))

    def tearDown(self):
        self.tmp.cleanup()

    def test_rate_and_eta(self):
        now = time.time()
        view1 = self.miner.update(now, str(self.dir), "python train.py", [])
        self.assertIsNotNone(view1)
        self.assertEqual(view1.pct, 10.0)
        self.log.write_text("Epoch 1/10 loss=1.0\nEpoch 3/10 loss=0.9\n",
                            encoding="utf-8")
        view2 = self.miner.update(now + 10, str(self.dir), "python train.py", [])
        self.assertIsNotNone(view2)
        self.assertEqual(view2.pct, 30.0)
        self.assertIsNotNone(view2.rate)
        # rate 的单位是“进度单位/秒”（epoch 3-1 = 2，10 秒）
        self.assertAlmostEqual(view2.rate, 0.2, places=3)
        self.assertAlmostEqual(view2.eta_seconds, (10 - 3) / 0.2, places=3)

    def test_reset_on_new_phase(self):
        now = time.time()
        self.miner.update(now, str(self.dir), "python train.py", [])
        self.log.write_text("Epoch 2/10\n", encoding="utf-8")
        self.miner.update(now + 5, str(self.dir), "python train.py", [])
        # 新阶段从头开始（数值变小），应重置采样
        self.log.write_text("Epoch 1/20\n", encoding="utf-8")
        view = self.miner.update(now + 10, str(self.dir), "python train.py", [])
        self.assertIsNotNone(view)
        self.assertLessEqual(len(self.miner.state.samples), 1)


class EpochCombineTest(unittest.TestCase):
    def test_extract_total_epochs(self):
        self.assertEqual(progress.extract_total_epochs("python train.py --epochs 80", ""), 80)
        self.assertEqual(progress.extract_total_epochs(
            "python train.py --num_epochs=120", ""), 120)
        self.assertEqual(progress.extract_total_epochs("python train.py", "epochs=50\n"), 50)
        self.assertIsNone(progress.extract_total_epochs("python train.py", ""))

    def test_combine_inner_tqdm(self):
        text = ("Epoch 65, CLS 0.8030, Mod. 0.0014:  57%|████████    | "
                "4/7 [00:05<00:03, 1.24s/it]")
        parsed = progress.parse_tail_text(text)
        combined = progress.combine_epoch_progress(parsed, text, 80)
        self.assertAlmostEqual(combined.cur, 64 + 4 / 7, places=3)
        self.assertEqual(combined.total, 80)
        self.assertAlmostEqual(combined.pct, (64 + 4 / 7) / 80 * 100, places=3)
        self.assertEqual(combined.display_label, "Epoch 65/80 · 57%")
        self.assertIsNone(combined.tqdm_eta)

    def test_combine_without_epoch_ignores_inner_eta(self):
        text = "57%|████████    | 4/7 [00:05<00:03, 1.24s/it]"
        parsed = progress.parse_tail_text(text)
        self.assertIsNotNone(parsed.tqdm_eta)
        combined = progress.combine_epoch_progress(parsed, text, 80)
        self.assertIsNone(combined.tqdm_eta)


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cwd = self.dir / "proj"
        self.cwd.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_redirect_log_discovered(self):
        log = self.dir / "proj" / "logs" / "train.log"
        log.parent.mkdir(parents=True)
        log.write_text("step 100/1000\n", encoding="utf-8")
        cmdline = f'python train.py > "{log}"'
        found = progress.discover_log(str(self.cwd), cmdline, [], time.time() - 60)
        self.assertEqual(os.path.normcase(found or ""), os.path.normcase(str(log)))

    def test_scan_finds_progress_log(self):
        log = self.cwd / "results" / "exp" / "stage.log"
        log.parent.mkdir(parents=True)
        log.write_text("  500/7442  fallback_so_far=0\n", encoding="utf-8")
        found = progress.discover_log(str(self.cwd), "python extract_vision.py",
                                      [], time.time() - 60)
        self.assertEqual(os.path.normcase(found or ""), os.path.normcase(str(log)))

    def test_irrelevant_text_file_not_picked(self):
        (self.cwd / "notes.txt").write_text("hello world\n", encoding="utf-8")
        found = progress.discover_log(str(self.cwd), "python extract_vision.py",
                                      [], time.time() - 60)
        self.assertIsNone(found)

    def test_log_in_script_dir_found_when_cwd_elsewhere(self):
        project = self.dir / "project"
        scripts = project / "scripts"
        logs = project / "logs" / "confirm"
        scripts.mkdir(parents=True)
        logs.mkdir(parents=True)
        (scripts / "merge_confirm_train.py").write_text("print('x')\n", encoding="utf-8")
        log = logs / "run.log"
        log.write_text("fold 22/50 seed44_fold1 27.9s\n", encoding="utf-8")
        cmdline = f'python "{scripts / "merge_confirm_train.py"}" --seed 542'
        found = progress.discover_log(str(self.cwd), cmdline, [], time.time() - 60)
        self.assertEqual(os.path.normcase(found or ""), os.path.normcase(str(log)))

    def test_prefers_log_inside_cwd_over_sibling(self):
        (self.cwd / "train.log").write_text("step 100/1000\n", encoding="utf-8")
        sibling = self.dir / "sibling" / "train.log"
        sibling.parent.mkdir(parents=True)
        sibling.write_text("step 900/1000\n", encoding="utf-8")
        # 兄弟目录的日志更新，但不能抢走本实验的日志
        os.utime(sibling, (time.time(), time.time()))
        found = progress.discover_log(str(self.cwd), "python train.py",
                                      [], time.time() - 120)
        self.assertEqual(os.path.normcase(found or ""),
                         os.path.normcase(str(self.cwd / "train.log")))

    def test_redirect_with_shell_variable_expands_and_uses_seed(self):
        temp = self.dir / "temp"
        temp.mkdir()
        correct = temp / "kd_meld_42.log"
        correct.write_text("fold 41/58 done\n", encoding="utf-8")
        wrong = temp / "pkg_meld_42.log"
        wrong.write_text("fold 58/58 done\n", encoding="utf-8")
        cmdline = ("python fa_kd_baseline.py --seed_base 42 "
                   "> /tmp/kd_meld_$s.log 2>&1")
        with mock.patch.dict(os.environ, {"TEMP": str(temp), "TMP": str(temp)}):
            found = progress.discover_log(str(self.cwd), cmdline, [], time.time() - 60)
        self.assertEqual(os.path.normcase(found or ""), os.path.normcase(str(correct)))


if __name__ == "__main__":
    unittest.main()
