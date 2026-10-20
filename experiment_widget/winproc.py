"""Windows 进程 / 系统信息探测（纯 ctypes，无第三方依赖）。

提供：进程快照（CPU 时间、内存、exe、父进程、会话）、命令行、工作目录、
系统 CPU 时间、物理内存、虚拟屏幕、进程结束、DPI 感知等。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import sys
import threading
import time
from typing import Iterable, Optional

from .models import ProcessStat

IS_64 = sys.maxsize > 2**32

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

# ---- 常量 -----------------------------------------------------------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_TERMINATE = 0x0001
PROCESS_SUSPEND_RESUME = 0x0800

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260
ERROR_ALREADY_EXISTS = 183
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
PROCESS_COMMAND_LINE_INFORMATION = 60
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

# ---- 结构体 ---------------------------------------------------------------
class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", w.DWORD), ("dwHighDateTime", w.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", w.DWORD),
        ("cntUsage", w.DWORD),
        ("th32ProcessID", w.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", w.DWORD),
        ("cntThreads", w.DWORD),
        ("th32ParentProcessID", w.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", w.DWORD),
        ("szExeFile", w.WCHAR * MAX_PATH),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("PageFaultCount", w.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_size_t),
        ("Reserved3", ctypes.c_void_p),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", w.DWORD),
        ("dwMemoryLoad", w.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


# ---- 函数签名 -------------------------------------------------------------
_k32.OpenProcess.restype = ctypes.c_void_p
_k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
_k32.CloseHandle.restype = w.BOOL
_k32.CloseHandle.argtypes = [ctypes.c_void_p]
_k32.GetProcessTimes.restype = w.BOOL
_k32.GetProcessTimes.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
]
_k32.QueryFullProcessImageNameW.restype = w.BOOL
_k32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p,
    w.DWORD,
    w.LPWSTR,
    ctypes.POINTER(w.DWORD),
]
_k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_k32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
_k32.Process32FirstW.restype = w.BOOL
_k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.restype = w.BOOL
_k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
_k32.GetSystemTimes.restype = w.BOOL
_k32.GetSystemTimes.argtypes = [
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
]
_k32.GetActiveProcessorCount.restype = w.DWORD
_k32.GetActiveProcessorCount.argtypes = [w.WORD]
_k32.GlobalMemoryStatusEx.restype = w.BOOL
_k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
_k32.ProcessIdToSessionId.restype = w.BOOL
_k32.ProcessIdToSessionId.argtypes = [w.DWORD, ctypes.POINTER(w.DWORD)]
_k32.GetCurrentProcessId.restype = w.DWORD
_k32.GetCurrentProcessId.argtypes = []
_k32.TerminateProcess.restype = w.BOOL
_k32.TerminateProcess.argtypes = [ctypes.c_void_p, w.UINT]
_k32.ReadProcessMemory.restype = w.BOOL
_k32.ReadProcessMemory.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_k32.CreateMutexW.restype = ctypes.c_void_p
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]

_ntdll.NtQueryInformationProcess.restype = ctypes.c_long
_ntdll.NtQueryInformationProcess.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong),
]
_ntdll.NtSuspendProcess.restype = ctypes.c_long
_ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
_ntdll.NtResumeProcess.restype = ctypes.c_long
_ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]

_psapi.GetProcessMemoryInfo.restype = w.BOOL
_psapi.GetProcessMemoryInfo.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
    w.DWORD,
]

_user32.GetSystemMetrics.restype = w.INT
_user32.GetSystemMetrics.argtypes = [w.INT]
_user32.FindWindowW.restype = ctypes.c_void_p
_user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
_user32.ShowWindow.restype = w.BOOL
_user32.ShowWindow.argtypes = [ctypes.c_void_p, w.INT]
_user32.SetForegroundWindow.restype = w.BOOL
_user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]

# ---- 内部缓存 -------------------------------------------------------------
_meta_lock = threading.Lock()
_exe_cache: dict[tuple[int, float], str] = {}
_session_cache: dict[int, int] = {}
_cmdline_cache: dict[tuple[int, float], Optional[str]] = {}
_cwd_cache: dict[tuple[int, float], tuple[float, Optional[str]]] = {}
_cache_clock = 0.0

_COUNTER_120NS = 10000000


def _mask(status: int) -> int:
    return status & 0xFFFFFFFF


def _open(pid: int, access: int) -> Optional[int]:
    if pid <= 0:
        return None
    handle = _k32.OpenProcess(access, False, pid)
    return handle or None


def _close(handle) -> None:
    if handle:
        _k32.CloseHandle(handle)


def _filetime_seconds(ft: FILETIME) -> float:
    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / _COUNTER_120NS


def filetime_to_unix(ft: FILETIME) -> float:
    """FILETIME(1601) -> unix 时间戳（秒）。"""
    ticks = ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) - 116444736000000000
    return ticks / _COUNTER_120NS


def _session_id(pid: int) -> int:
    cached = _session_cache.get(pid)
    if cached is not None:
        return cached
    sid = w.DWORD(0)
    if _k32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
        val = sid.value & 0xFFFFFFFF
        _session_cache[pid] = val
        return val
    return -1


def current_session_id() -> int:
    return _session_id(os.getpid())


def _process_exe(pid: int, ident: tuple[int, float]) -> str:
    cached = _exe_cache.get(ident)
    if cached is not None:
        return cached
    path = ""
    handle = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if handle:
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = w.DWORD(len(buf))
            if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                path = buf.value
        finally:
            _close(handle)
    _exe_cache[ident] = path
    return path


def _process_mem(handle) -> int:
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(counters)
    if _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return int(counters.WorkingSetSize)
    return 0


def snapshot_processes(only_session: Optional[int] = None) -> dict[int, ProcessStat]:
    """枚举当前全部进程，返回 pid -> ProcessStat。"""
    global _cache_clock
    _cache_clock = time.monotonic()
    result: dict[int, ProcessStat] = {}
    snapshot = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return result
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = entry.th32ProcessID & 0xFFFFFFFF
            if pid:
                sid = _session_id(pid)
                if only_session is None or sid == only_session:
                    stat = _read_process(pid, entry.th32ParentProcessID & 0xFFFFFFFF,
                                         entry.szExeFile, entry.cntThreads, sid)
                    if stat is not None:
                        result[pid] = stat
            ok = _k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _close(snapshot)
    # 清理缓存，避免长时间运行后无限增长
    if len(_exe_cache) > 6000:
        _exe_cache.clear()
    if len(_cmdline_cache) > 6000:
        _cmdline_cache.clear()
    return result


def _read_process(pid: int, ppid: int, name: str, threads: int,
                  session_id: int) -> Optional[ProcessStat]:
    handle = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return None
    try:
        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
            return None
        create_ts = filetime_to_unix(created)
        cpu_time = _filetime_seconds(kernel) + _filetime_seconds(user)
        ident = (pid, round(create_ts, 3))
        exe = _process_exe(pid, ident)
        mem = _process_mem(handle)
        return ProcessStat(
            pid=pid,
            ppid=ppid,
            name=name or (os.path.basename(exe) if exe else "?"),
            exe=exe,
            create_time=create_ts,
            cpu_time=cpu_time,
            mem_rss=mem,
            threads=threads,
            session_id=session_id,
        )
    finally:
        _close(handle)


# ---- 命令行 / 工作目录 ----------------------------------------------------
def get_command_line(pid: int, create_time: float = 0.0) -> Optional[str]:
    """读取进程完整命令行（NtQueryInformationProcess）。

    注意：读取失败时不做负缓存，进程刚启动时可能暂时读不到，稍后重试即可。
    """
    ident = (pid, round(create_time, 3))
    cached = _cmdline_cache.get(ident)
    if cached:
        return cached
    cmdline: Optional[str] = None
    handle = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        handle = _open(pid, PROCESS_QUERY_INFORMATION)
    if handle:
        try:
            cmdline = _query_cmdline(handle)
        finally:
            _close(handle)
    if cmdline:
        _cmdline_cache[ident] = cmdline
    return cmdline


def _query_cmdline(handle) -> Optional[str]:
    size = 4096
    for _ in range(4):
        buf = ctypes.create_string_buffer(size)
        ret = ctypes.c_ulong(0)
        status = _mask(_ntdll.NtQueryInformationProcess(
            handle, PROCESS_COMMAND_LINE_INFORMATION, buf, size, ctypes.byref(ret)))
        if status == 0:
            base = ctypes.cast(buf, ctypes.c_void_p).value
            if not base:
                return None
            length = ctypes.cast(base, ctypes.POINTER(ctypes.c_ushort))[0]
            ptr = ctypes.cast(base + 8, ctypes.POINTER(ctypes.c_void_p)).contents.value
            if not ptr or length <= 0 or length > 65534:
                return None
            try:
                return ctypes.wstring_at(ptr, length // 2)
            except OSError:
                return None
        if status == STATUS_INFO_LENGTH_MISMATCH:
            size = max(size, int(ret.value) + 16)
            continue
        # 部分进程（如受保护进程）需要更高权限，退回 WMI 查询
        return _cmdline_via_wmi(handle)
    return None


def _cmdline_via_wmi(_handle=None) -> Optional[str]:
    """命令行读取失败时的低优先级兜底（WMI/CIM），较慢，只在少数进程上用。"""
    try:
        pid = _pid_from_handle(_handle) if _handle else None
        if not pid:
            return None
        from .util import run_powershell

        out = run_powershell(
            f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine',
            timeout=4.0)
        text = (out or "").strip()
        return text or None
    except Exception:
        return None


def _pid_from_handle(handle) -> Optional[int]:
    try:
        pid = w.DWORD(0)
        fn = _k32.GetProcessId
        fn.restype = w.DWORD
        fn.argtypes = [ctypes.c_void_p]
        val = fn(handle) & 0xFFFFFFFF
        return val or None
    except Exception:
        return None


def get_cwd(pid: int, create_time: float = 0.0, ttl: float = 300.0) -> Optional[str]:
    """读取进程当前工作目录（读取 PEB，需 64 位 Python）。"""
    ident = (pid, round(create_time, 3))
    now = time.monotonic()
    cached = _cwd_cache.get(ident)
    if cached and now - cached[0] < ttl:
        return cached[1]
    cwd = _read_cwd(pid)
    _cwd_cache[ident] = (now, cwd)
    if len(_cwd_cache) > 3000:
        _cwd_cache.clear()
    return cwd


def _read_cwd(pid: int) -> Optional[str]:
    if not IS_64:
        return None
    handle = _open(pid, PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)
    if not handle:
        return None
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        ret = ctypes.c_ulong(0)
        status = _mask(_ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret)))
        if status != 0 or not pbi.PebBaseAddress:
            return None
        params_ptr = _read_process_pointer(handle, pbi.PebBaseAddress + 0x20)
        if not params_ptr:
            return None
        cur = _read_bytes(handle, params_ptr + 0x38, 16)
        if not cur:
            return None
        length = ctypes.cast(cur, ctypes.POINTER(ctypes.c_ushort))[0]
        if length <= 0 or length > 32768:
            return None
        buf_ptr = ctypes.cast(cur[8:16], ctypes.POINTER(ctypes.c_void_p)).contents.value
        if not buf_ptr:
            return None
        raw = _read_bytes(handle, buf_ptr, length)
        if not raw:
            return None
        path = raw.decode("utf-16-le", errors="replace").strip("\x00")
        path = path.replace("\\\\?\\", "")
        return path or None
    except Exception:
        return None
    finally:
        _close(handle)


def _read_process_pointer(handle, address: int) -> Optional[int]:
    raw = _read_bytes(handle, address, ctypes.sizeof(ctypes.c_void_p))
    if not raw:
        return None
    return int.from_bytes(raw, "little")


def _read_bytes(handle, address: int, size: int) -> Optional[bytes]:
    if not address or size <= 0:
        return None
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    ok = _k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(got))
    if not ok or got.value != size:
        return None
    return buf.raw


def is_process_alive(pid: int, create_time: float = 0.0) -> bool:
    handle = _open(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        created = FILETIME()
        exited = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
            return False
        if create_time:
            return abs(filetime_to_unix(created) - create_time) < 1.0
        return True
    finally:
        _close(handle)


# ---- 结束进程 -------------------------------------------------------------
def terminate_process_tree(root_pid: int, procs: Optional[dict[int, ProcessStat]] = None) -> list[int]:
    """结束 root_pid 及其全部后代进程（先子后父）。返回已结束的 pid 列表。"""
    from .util import log_line

    if procs is None:
        procs = snapshot_processes()
    children: dict[int, list[int]] = {}
    for stat in procs.values():
        children.setdefault(stat.ppid, []).append(stat.pid)

    ordered: list[int] = []

    def walk(pid: int, depth: int = 0) -> None:
        if depth > 8:
            return
        for child in children.get(pid, []):
            walk(child, depth + 1)
        ordered.append(pid)

    walk(root_pid)
    killed: list[int] = []
    for pid in ordered:
        handle = _open(pid, PROCESS_TERMINATE)
        if not handle:
            continue
        try:
            if _k32.TerminateProcess(handle, 1):
                killed.append(pid)
        finally:
            _close(handle)
    log_line(f"terminate tree {root_pid}: killed={killed}")
    return killed


# ---- 挂起 / 恢复进程（安全暂停） ------------------------------------------
def suspend_process(pid: int) -> bool:
    """冻结单个进程的全部线程（状态保留在内存中，可恢复）。"""
    handle = _open(pid, PROCESS_SUSPEND_RESUME | PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        status = _mask(_ntdll.NtSuspendProcess(handle))
        return status == 0
    except Exception:
        return False
    finally:
        _close(handle)


def resume_process(pid: int) -> bool:
    handle = _open(pid, PROCESS_SUSPEND_RESUME | PROCESS_QUERY_LIMITED_INFORMATION)
    if not handle:
        return False
    try:
        status = _mask(_ntdll.NtResumeProcess(handle))
        return status == 0
    except Exception:
        return False
    finally:
        _close(handle)


def suspend_processes(pids: list[int]) -> tuple[list[int], list[int]]:
    """按“根进程优先”顺序挂起一组进程。返回 (成功, 失败)。"""
    suspended: list[int] = []
    failed: list[int] = []
    for pid in pids:
        if suspend_process(pid):
            suspended.append(pid)
        else:
            failed.append(pid)
    return suspended, failed


def resume_processes(pids: list[int]) -> tuple[list[int], list[int]]:
    """按“子进程优先、根进程最后”的顺序恢复一组进程。"""
    resumed: list[int] = []
    failed: list[int] = []
    for pid in reversed(pids):
        if resume_process(pid):
            resumed.append(pid)
        else:
            failed.append(pid)
    return resumed, failed


# ---- 系统信息 -------------------------------------------------------------
_last_system: Optional[tuple[float, int, int, int]] = None
_system_lock = threading.Lock()


def system_cpu_pct() -> float:
    """整机 CPU 使用率（0~100），需要两次采样。"""
    global _last_system
    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return 0.0
    vals = (
        time.monotonic(),
        int((idle.dwHighDateTime << 32) | idle.dwLowDateTime),
        int((kernel.dwHighDateTime << 32) | kernel.dwLowDateTime),
        int((user.dwHighDateTime << 32) | user.dwLowDateTime),
    )
    with _system_lock:
        prev = _last_system
        _last_system = vals
    if not prev:
        return 0.0
    dt = vals[0] - prev[0]
    if dt <= 0:
        return 0.0
    idle_delta = (vals[1] - prev[1]) / _COUNTER_120NS
    total_delta = ((vals[2] - prev[2]) + (vals[3] - prev[3])) / _COUNTER_120NS
    if total_delta <= 0:
        return 0.0
    pct = (1.0 - idle_delta / total_delta) * 100.0
    return max(0.0, min(100.0, pct))


_cpu_count: Optional[int] = None


def cpu_count() -> int:
    global _cpu_count
    if _cpu_count is None:
        try:
            value = _k32.GetActiveProcessorCount(0xFFFF)
            _cpu_count = int(value) or (os.cpu_count() or 1)
        except Exception:
            _cpu_count = os.cpu_count() or 1
        _cpu_count = max(1, _cpu_count)
    return _cpu_count


def memory_info() -> tuple[float, float]:
    """返回 (已用 MB, 总 MB)。"""
    mem = MEMORYSTATUSEX()
    mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not _k32.GlobalMemoryStatusEx(ctypes.byref(mem)):
        return 0.0, 0.0
    total_mb = mem.ullTotalPhys / 1048576.0
    used_mb = (mem.ullTotalPhys - mem.ullAvailPhys) / 1048576.0
    return used_mb, total_mb


def virtual_screen_rect() -> tuple[int, int, int, int]:
    try:
        return (
            _user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            _user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
        )
    except Exception:
        return 0, 0, 1920, 1080


# ---- 窗口 / 单实例 --------------------------------------------------------
def set_dpi_awareness() -> None:
    """必须在创建 Tk 窗口前调用，避免高 DPI 下界面模糊。"""
    try:
        shcore = ctypes.WinDLL("shcore")
        shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


def screen_dpi() -> float:
    try:
        dc = _user32.GetDC(None)
        dpi = _gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
        _user32.ReleaseDC(None, dc)
        return float(dpi) if dpi else 96.0
    except Exception:
        return 96.0


try:
    _gdi32 = ctypes.WinDLL("gdi32")
    _gdi32.GetDeviceCaps.restype = ctypes.c_int
    _gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetDC.restype = ctypes.c_void_p
    _user32.GetDC.argtypes = [ctypes.c_void_p]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
except Exception:
    pass


def acquire_single_instance(name: str):
    """创建命名互斥量；若已存在返回 None。成功时返回句柄（需保持引用）。"""
    try:
        handle = _k32.CreateMutexW(None, False, name)
        if not handle:
            return None
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _k32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return None


def bring_window_to_front(title: str) -> bool:
    try:
        hwnd = _user32.FindWindowW(None, title)
        if not hwnd:
            return False
        _user32.ShowWindow(hwnd, 5)
        _user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
