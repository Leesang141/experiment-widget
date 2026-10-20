"""实验描述 / 框架自动识别测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiment_widget.descriptions import DescriptionStore


class DescriptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.folder = self.base / "proj"
        self.folder.mkdir()
        (self.folder / "requirements.txt").write_text(
            "torch==2.4.0\ntransformers==4.44\nnumpy\n", encoding="utf-8")
        self.store = DescriptionStore(self.base / "descriptions.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_framework_from_requirements(self):
        framework, _source = self.store.detect_framework(
            str(self.folder), "python train.py", r"C:\Python\python.exe")
        self.assertIn("PyTorch", framework)
        self.assertIn("HuggingFace", framework)

    def test_detect_framework_from_conda_env(self):
        framework, _source = self.store.detect_framework(
            r"D:\other", "python train.py",
            r"C:\miniconda3\envs\torch\python.exe")
        self.assertIn("PyTorch", framework)

    def test_detect_framework_fallback(self):
        framework, _ = self.store.detect_framework(
            str(self.base), "python x.py", r"C:\Python\python.exe")
        self.assertEqual(framework, "Python")

    def test_auto_idea_from_experiment_json(self):
        (self.folder / ".experiment.json").write_text(
            json.dumps({"idea": "候选10：补 8 下界实验", "framework": "PyTorch"}),
            encoding="utf-8")
        meta = self.store.folder_meta(str(self.folder))
        self.assertEqual(meta["idea"], "候选10：补 8 下界实验")
        resolved = self.store.resolve("fp", str(self.folder), "python train.py", "python.exe")
        self.assertEqual(resolved["idea"], "候选10：补 8 下界实验")
        self.assertEqual(resolved["description_source"], "项目文件")

    def test_auto_idea_from_readme(self):
        (self.folder / "README.md").write_text(
            "# 候选11 · 温度消融\n\n一些说明\n", encoding="utf-8")
        meta = self.store.folder_meta(str(self.folder))
        self.assertEqual(meta["idea"], "候选11 · 温度消融")

    def test_manual_description_persists(self):
        self.store.set("fp", "手动想法", "自定义框架", "备注内容")
        reloaded = DescriptionStore(self.base / "descriptions.json")
        entry = reloaded.get("fp")
        self.assertEqual(entry["idea"], "手动想法")
        self.assertEqual(entry["framework"], "自定义框架")
        self.assertEqual(entry["note"], "备注内容")
        resolved = reloaded.resolve("fp", str(self.folder), "python train.py", "python.exe")
        self.assertEqual(resolved["idea"], "手动想法")
        self.assertEqual(resolved["framework"], "自定义框架")
        self.assertEqual(resolved["description_source"], "自定义")

    def test_clear_manual_description(self):
        self.store.set("fp", "想法", "框架", "备注")
        self.store.clear("fp")
        self.assertIsNone(self.store.get("fp"))

    def test_manual_empty_idea_suppresses_auto_idea(self):
        (self.folder / "README.md").write_text("# 自动想法\n", encoding="utf-8")
        self.store.set("fp", "", "PyTorch", "")
        resolved = self.store.resolve("fp", str(self.folder), "python x.py", "python.exe")
        self.assertEqual(resolved["idea"], "")


if __name__ == "__main__":
    unittest.main()
