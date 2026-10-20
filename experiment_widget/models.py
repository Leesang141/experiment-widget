"""跨模块共享的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 系统 / 进程
# ---------------------------------------------------------------------------
@dataclass
class ProcessStat:
    """一次进程快照。cpu_time 为累计 CPU 时间（秒，kernel+user）。"""

    pid: int
    ppid: int
    name: str
    exe: str
    create_time: float  # unix 时间戳
    cpu_time: float
    mem_rss: int  # bytes
    threads: int
    session_id: int
    cpu_pct: float = 0.0  # 由 Tracker 根据两次采样差计算，占整机百分比

    @property
    def ident(self) -> tuple[int, float]:
        """PID 会被复用，用 (pid, 创建时间) 作为身份。"""
        return (self.pid, round(self.create_time, 3))


@dataclass
class GpuDevice:
    index: int
    name: str
    util_pct: float = 0.0
    mem_used_mb: float = 0.0
    mem_total_mb: float = 0.0
    temp_c: Optional[float] = None


@dataclass
class GpuSnapshot:
    """GPU 采集结果。per_pid_* 是进程维度数据，devices 是整卡数据。"""

    devices: list[GpuDevice] = field(default_factory=list)
    per_pid_util: dict[int, float] = field(default_factory=dict)
    per_pid_mem_mb: dict[int, float] = field(default_factory=dict)
    util_source: dict[int, str] = field(default_factory=dict)  # pid -> 'pmon' / 'engine'
    timestamp: float = 0.0
    nvidia_ok: bool = False
    windows_ok: bool = False
    pmon_ok: bool = False
    error: str = ""

    @property
    def device_util(self) -> Optional[float]:
        if not self.devices:
            return None
        return max(d.util_pct for d in self.devices)

    @property
    def vram_used_mb(self) -> Optional[float]:
        if not self.devices:
            return None
        return sum(d.mem_used_mb for d in self.devices)

    @property
    def vram_total_mb(self) -> Optional[float]:
        if not self.devices:
            return None
        return sum(d.mem_total_mb for d in self.devices)


# ---------------------------------------------------------------------------
# 估算
# ---------------------------------------------------------------------------
@dataclass
class Estimate:
    """预计剩余时间。seconds 为 None 表示未知。"""

    seconds: Optional[float] = None
    basis: str = "none"  # progress / tqdm / history / manual / none
    confidence: str = "none"  # high / medium / low / none
    detail: str = ""
    total_seconds: Optional[float] = None
    progress_pct: Optional[float] = None
    basis_label: str = ""


@dataclass
class ProgressView:
    pct: float
    label: str
    rate: Optional[float] = None
    eta_seconds: Optional[float] = None
    source: str = ""
    stale: bool = False
    current: Optional[float] = None
    total: Optional[float] = None
    kind: str = "fraction"
    tqdm_eta: Optional[float] = None


# ---------------------------------------------------------------------------
# 整理后供 UI 使用的数据
# ---------------------------------------------------------------------------
@dataclass
class ExperimentView:
    key: str  # 会话唯一键（pid@create_time）
    fingerprint: str
    name: str
    cmdline: str
    folder: str
    folder_source: str  # peb / psutil / cmdline / inferred / unknown
    exe: str
    pids: list[int]
    started_at: float
    elapsed: float
    cpu_pct: float
    gpu_util: Optional[float]
    gpu_mem_mb: Optional[float]
    mem_mb: float
    gpu_source: str  # pmon / engine / none
    idle_seconds: float
    estimate: Optional[Estimate] = None
    progress: Optional[ProgressView] = None
    log_path: Optional[str] = None
    manual_total: Optional[float] = None
    hidden: bool = False
    # -- 实验描述 ------------------------------------------------------
    framework: str = ""           # 自动/自定义框架，例如 PyTorch
    idea: str = ""                # 这个实验对应哪个想法/候选
    note: str = ""                # 备注
    description_source: str = ""  # 自动识别 / 项目文件 / 自定义
    paused: bool = False
    paused_seconds: float = 0.0

    @property
    def idle(self) -> bool:
        return self.idle_seconds >= 30.0


@dataclass
class FolderView:
    folder: str
    display: str
    experiments: list[ExperimentView] = field(default_factory=list)
    cpu_pct: float = 0.0
    gpu_util: Optional[float] = None
    gpu_mem_mb: Optional[float] = None
    count: int = 0


@dataclass
class SystemView:
    cpu_pct: float = 0.0
    gpu_util: Optional[float] = None
    gpu_count: int = 0
    vram_used_mb: Optional[float] = None
    vram_total_mb: Optional[float] = None
    ram_used_mb: Optional[float] = None
    ram_total_mb: Optional[float] = None
    gpu_error: str = ""


@dataclass
class Snapshot:
    ts: float
    system: SystemView
    folders: list[FolderView] = field(default_factory=list)
    total_experiments: int = 0
    hidden_experiments: int = 0
    paused_experiments: int = 0
    history_count: int = 0
    sampler_error: str = ""
    demo: bool = False

    @property
    def experiment_count(self) -> int:
        return self.total_experiments
