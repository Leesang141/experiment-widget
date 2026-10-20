"""GPU 解析与 Windows 计数器测试。"""

from __future__ import annotations

import os
import unittest

from experiment_widget import gpu


class ParseTest(unittest.TestCase):
    def test_devices_csv(self):
        text = (
            "0, NVIDIA GeForce RTX 4060 Laptop GPU, 44, 3247, 8188, 54\n"
            "1, Intel UHD Graphics, 3, 256, 8192, [N/A]\n"
        )
        devices = gpu.parse_devices_csv(text)
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].name, "NVIDIA GeForce RTX 4060 Laptop GPU")
        self.assertEqual(devices[0].util_pct, 44)
        self.assertEqual(devices[0].mem_used_mb, 3247)
        self.assertEqual(devices[0].temp_c, 54)
        self.assertEqual(devices[1].temp_c, 0)

    def test_devices_csv_invalid(self):
        self.assertEqual(gpu.parse_devices_csv(""), [])
        self.assertEqual(gpu.parse_devices_csv("garbage"), [])

    def test_pmon_parse(self):
        text = (
            "# gpu         pid   type     sm    mem    enc    dec    jpg    ofa    command\n"
            "# Idx           #    C/G      %      %      %      %      %      %    name\n"
            "    0      40128     C     29      5      -      -      -      -    python.exe\n"
            "    0      65436     C      4      0      -      -      -      -    python.exe\n"
            "    0       7736   C+G      6      1      -      -      -      -    DeepSeek\n"
        )
        values = gpu.parse_pmon(text)
        self.assertEqual(values.get(40128), 29.0)
        self.assertEqual(values.get(65436), 4.0)
        self.assertEqual(values.get(7736), 6.0)

    def test_instance_pid(self):
        name = "pid_12345_luid_0x00000000_0x00011EBC_phys_0_eng_0_engtype_3D"
        self.assertEqual(gpu._instance_pid(name), 12345)
        name = "pid_99_luid_0x00000000_0x00011EBC_phys_0"
        self.assertEqual(gpu._instance_pid(name), 99)
        self.assertIsNone(gpu._instance_pid("gpu_engine"))


@unittest.skipUnless(os.environ.get("WIDGET_LIVE_TESTS"), "需要真实 Windows/GPU 环境")
class LiveGpuTest(unittest.TestCase):
    def test_nvidia_smi(self):
        smi = gpu.nvidia_smi()
        if not smi:
            self.skipTest("nvidia-smi 不可用")
        devices = gpu.query_devices(smi)
        self.assertGreaterEqual(len(devices), 1)
        self.assertGreater(devices[0].mem_total_mb, 0)

    def test_windows_counters(self):
        counters = gpu.WindowsGpuCounters()
        self.addCleanup(counters.close)
        if not counters.available:
            self.skipTest(f"PDH 不可用：{counters.error}")
        util, ded, shared = counters.sample()
        self.assertIsInstance(util, dict)
        self.assertIsInstance(ded, dict)

    def test_poll_once(self):
        poller = gpu.GpuPoller(interval=2.0)
        snapshot = poller.poll_once()
        self.assertIsNotNone(snapshot)


if __name__ == "__main__":
    unittest.main()
