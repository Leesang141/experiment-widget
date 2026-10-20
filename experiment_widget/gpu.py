"""GPU 数据采集。

两种数据源：
1. ``nvidia-smi``：整卡利用率 / 显存 / 温度（NVIDIA 独显），
   以及 ``nvidia-smi pmon`` 的进程 SM 利用率；
2. Windows 性能计数器（PDH 原生读取，开销极低）：
   ``GPU Engine`` 每进程 GPU 引擎活动、``GPU Process Memory`` 每进程显存。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import re
import shutil
import threading
import time
from typing import Optional

from .models import GpuDevice, GpuSnapshot
from .util import log_line, run_process

PDH_FMT_DOUBLE = 0x00000200
PDH_MORE_DATA = 0x800007D2

COUNTER_ENGINE = r"\GPU Engine(*)\Utilization Percentage"
COUNTER_MEM_DEDICATED = r"\GPU Process Memory(*)\Dedicated Usage"
COUNTER_MEM_SHARED = r"\GPU Process Memory(*)\Shared Usage"

_ENGINE_PID_RE = re.compile(r"^pid_(\d+)_")
_PMON_PID_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+([CG])\+?([CG]?)\s+(\S+)")


class PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [("CStatus", w.DWORD), ("doubleValue", ctypes.c_double)]


class PDH_FMT_COUNTERVALUE_ITEM_W(ctypes.Structure):
    _fields_ = [("szName", w.LPWSTR), ("FmtValue", PDH_FMT_COUNTERVALUE)]


class WindowsGpuCounters:
    """通过 PDH 读取每个进程的 GPU 引擎利用率与显存占用。"""

    def __init__(self) -> None:
        self._pdh = None
        self._query = None
        self._counters: list = []
        self._ok = False
        self.error = ""
        try:
            self._open()
        except Exception as exc:  # pragma: no cover - 依赖显卡/系统
            self.error = str(exc)
            log_line(f"PDH GPU counters init failed: {exc}")

    # -- 初始化 ---------------------------------------------------------
    def _open(self) -> None:
        pdh = ctypes.WinDLL("pdh", use_last_error=True)
        pdh.PdhOpenQueryW.restype = ctypes.c_ulong
        pdh.PdhOpenQueryW.argtypes = [w.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(w.HANDLE)]
        pdh.PdhAddEnglishCounterW.restype = ctypes.c_ulong
        pdh.PdhAddEnglishCounterW.argtypes = [
            w.HANDLE,
            w.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(w.HANDLE),
        ]
        pdh.PdhCollectQueryData.restype = ctypes.c_ulong
        pdh.PdhCollectQueryData.argtypes = [w.HANDLE]
        pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_ulong
        pdh.PdhGetFormattedCounterArrayW.argtypes = [
            w.HANDLE,
            w.DWORD,
            ctypes.POINTER(w.DWORD),
            ctypes.POINTER(w.DWORD),
            ctypes.c_void_p,
        ]
        pdh.PdhCloseQuery.restype = ctypes.c_ulong
        pdh.PdhCloseQuery.argtypes = [w.HANDLE]
        self._pdh = pdh
        query = w.HANDLE()
        if _rc(pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))) != 0:
            raise RuntimeError("PdhOpenQueryW failed")
        self._query = query
        for path in (COUNTER_ENGINE, COUNTER_MEM_DEDICATED, COUNTER_MEM_SHARED):
            counter = w.HANDLE()
            if _rc(pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter))) != 0:
                raise RuntimeError(f"PdhAddEnglishCounterW failed: {path}")
            self._counters.append(counter)
        self._collect()
        time.sleep(0.35)
        self._collect()
        self._ok = True

    def close(self) -> None:
        if self._pdh and self._query:
            try:
                self._pdh.PdhCloseQuery(self._query)
            except Exception:
                pass
            self._query = None

    # -- 采样 -----------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._ok

    def _collect(self) -> None:
        if self._pdh and self._query:
            _rc(self._pdh.PdhCollectQueryData(self._query))

    def sample(self) -> tuple[dict[int, float], dict[int, float], dict[int, float]]:
        """返回 (每进程引擎利用率 0-100, 每进程专用显存 MB, 每进程共享显存 MB)。"""
        if not self._ok:
            return {}, {}, {}
        self._collect()
        engine = _read_counter_array(self._pdh, self._counters[0])
        ded = _read_counter_array(self._pdh, self._counters[1])
        shared = _read_counter_array(self._pdh, self._counters[2])
        util: dict[int, float] = {}
        for name, value in engine:
            pid = _instance_pid(name)
            if pid is None or value <= 0:
                continue
            # 同一进程可能对应多个引擎实例（3D / Compute / Copy ...），求和
            util[pid] = min(100.0, util.get(pid, 0.0) + value)
        ded_mb: dict[int, float] = {}
        for name, value in ded:
            pid = _instance_pid(name)
            if pid is None or value <= 0:
                continue
            ded_mb[pid] = ded_mb.get(pid, 0.0) + value / 1048576.0
        shared_mb: dict[int, float] = {}
        for name, value in shared:
            pid = _instance_pid(name)
            if pid is None or value <= 0:
                continue
            shared_mb[pid] = shared_mb.get(pid, 0.0) + value / 1048576.0
        return util, ded_mb, shared_mb


def _rc(value) -> int:
    return int(value) & 0xFFFFFFFF


def _instance_pid(name: str) -> Optional[int]:
    match = _ENGINE_PID_RE.match(name or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _read_counter_array(pdh, counter) -> list[tuple[str, float]]:
    if not counter:
        return []
    size = w.DWORD(0)
    count = w.DWORD(0)
    rc = _rc(pdh.PdhGetFormattedCounterArrayW(
        counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None))
    if rc not in (0, PDH_MORE_DATA):
        return []
    if size.value <= 0:
        return []
    buf = ctypes.create_string_buffer(size.value)
    rc = _rc(pdh.PdhGetFormattedCounterArrayW(
        counter, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf))
    if rc != 0 or count.value <= 0:
        return []
    items = ctypes.cast(
        buf, ctypes.POINTER(PDH_FMT_COUNTERVALUE_ITEM_W * count.value)).contents
    out: list[tuple[str, float]] = []
    for item in items:
        try:
            out.append((item.szName or "", float(item.FmtValue.doubleValue)))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# nvidia-smi
# ---------------------------------------------------------------------------
_smi_path: Optional[str] = None
_smi_checked = False


def nvidia_smi() -> Optional[str]:
    global _smi_path, _smi_checked
    if not _smi_checked:
        candidates = [
            shutil.which("nvidia-smi.exe"),
            shutil.which("nvidia-smi"),
            r"C:\Windows\System32\nvidia-smi.exe",
            os.path.expandvars(r"%ProgramFiles%\NVIDIA Corporation\NVSMI\nvidia-smi.exe"),
            os.path.expandvars(r"%ProgramFiles%\NVIDIA Corporation\NVIDIA App\nvidia-smi.exe"),
        ]
        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                _smi_path = candidate
                break
        _smi_checked = True
        if not _smi_path:
            log_line("nvidia-smi not found")
    return _smi_path


def parse_devices_csv(text: str) -> list[GpuDevice]:
    devices: list[GpuDevice] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue

        def num(value: str) -> float:
            try:
                return float(value)
            except ValueError:
                return 0.0

        devices.append(GpuDevice(
            index=index,
            name=parts[1],
            util_pct=num(parts[2]),
            mem_used_mb=num(parts[3]),
            mem_total_mb=num(parts[4]),
            temp_c=num(parts[5]) if len(parts) > 5 else None,
        ))
    return devices


def query_devices(smi: str) -> list[GpuDevice]:
    out = run_process([
        smi,
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
        "--format=csv,noheader,nounits",
    ], timeout=5.0)
    if not out:
        return []
    return parse_devices_csv(out)


def parse_pmon(text: str) -> dict[int, float]:
    """解析 ``nvidia-smi pmon`` 输出，返回 pid -> SM 利用率（0-100）。"""
    result: dict[int, float] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # 格式: gpu  pid  type  sm  mem  enc  dec  jpg  ofa  command
        match = re.match(r"^\s*\d+\s+(\d+)\s+\S+\s+(\S+)", line)
        if not match:
            continue
        try:
            pid = int(match.group(1))
        except ValueError:
            continue
        raw = match.group(2)
        try:
            result[pid] = float(raw)
        except ValueError:
            result[pid] = 0.0
    return result


def query_pmon(smi: str) -> Optional[dict[int, float]]:
    out = run_process([smi, "pmon", "-c", "1"], timeout=6.0)
    if not out:
        return None
    return parse_pmon(out)


# ---------------------------------------------------------------------------
# 轮询线程
# ---------------------------------------------------------------------------
class GpuPoller(threading.Thread):
    """后台 GPU 采样线程，UI 线程通过 snapshot 属性读取最新结果。"""

    def __init__(self, interval: float = 2.5, enable_pmon: bool = True):
        super().__init__(name="gpu-poller", daemon=True)
        self.interval = max(1.0, float(interval))
        self.enable_pmon = enable_pmon
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = GpuSnapshot()
        self._windows: Optional[WindowsGpuCounters] = None

    # -- 生命周期 -------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    @property
    def snapshot(self) -> GpuSnapshot:
        with self._lock:
            return self._snapshot

    def run(self) -> None:
        self._windows = WindowsGpuCounters()
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                snap = self.poll_once()
            except Exception as exc:  # pragma: no cover
                log_line(f"gpu poll failed: {exc}")
                snap = GpuSnapshot(error=str(exc))
            snap.timestamp = time.time()
            with self._lock:
                self._snapshot = snap
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.5, self.interval - elapsed))
        if self._windows:
            self._windows.close()

    # -- 单次采样 -------------------------------------------------------
    def poll_once(self) -> GpuSnapshot:
        snap = GpuSnapshot()
        smi = nvidia_smi()
        if smi:
            devices = query_devices(smi)
            if devices:
                snap.devices = devices
                snap.nvidia_ok = True
            if self.enable_pmon:
                pmon = query_pmon(smi)
                if pmon is not None:
                    snap.pmon_ok = True
                    for pid, util in pmon.items():
                        snap.per_pid_util[pid] = util
                        snap.util_source[pid] = "pmon"
        if self._windows and self._windows.available:
            util, ded, shared = self._windows.sample()
            snap.windows_ok = True
            for pid, value in util.items():
                if pid not in snap.per_pid_util:
                    snap.per_pid_util[pid] = value
                    snap.util_source[pid] = "engine"
            for pid, value in ded.items():
                # 专用显存（VRAM）优先；共享显存仅作为没有专用显存时的兜底
                if value > 0:
                    snap.per_pid_mem_mb[pid] = value
            for pid, value in shared.items():
                if pid not in snap.per_pid_mem_mb and value > 8:
                    snap.per_pid_mem_mb[pid] = value
        else:
            snap.error = self._windows.error if self._windows else ""
        return snap
