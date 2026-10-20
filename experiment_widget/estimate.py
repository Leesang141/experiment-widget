"""实验识别、指标聚合与剩余时间估算。

Tracker 在采样线程里运行：
- 根据 CPU / GPU 活动识别“实验”进程，并把它启动的子孙进程（dataloader worker 等）
  聚合到同一个实验里；
- 读取工作目录、命令行，得到实验来源文件夹；
- 结合进度日志、手动设定、历史运行记录估算剩余时间；
- 进程结束时把运行时长写入历史。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import winproc
from .config import Config
from .descriptions import DescriptionStore
from .fmt import fmt_age_minutes, fmt_elapsed_long
from .history import HistoryStats, HistoryStore
from .pauses import PauseRegistry
from .models import (
    Estimate,
    ExperimentView,
    FolderView,
    GpuSnapshot,
    ProcessStat,
    ProgressView,
    Snapshot,
    SystemView,
)
from .progress import ProgressMiner, find_ancestor_cmdlines
from .util import basename, dirname, log_line, normalize_folder, strip_quotes

_SCRIPT_EXT_RE = re.compile(
    r"(?i)\.(?:py|pyw|ipynb|jl|sh|ps1|r|m|js|ts|cpp|cc|cxx|cu|c|rs|go|java)$")
_SCRIPT_TOKEN_RE = re.compile(
    r"(?i)\"[^\"]+\"|'[^']+'|\S+")
_MODULE_RE = re.compile(r"(?:^|\s)-m\s+([A-Za-z_][\w.]*)")


# ---------------------------------------------------------------------------
# 命令行 / 文件夹 / 指纹
# ---------------------------------------------------------------------------
def split_cmdline(cmdline: str) -> list[str]:
    return _SCRIPT_TOKEN_RE.findall(cmdline or "")


def experiment_title(cmdline: str, exe: str) -> str:
    """实验的展示名：脚本文件名 > -m 模块 > 可执行文件名。"""
    tokens = split_cmdline(cmdline)
    for token in tokens[1:]:
        clean = strip_quotes(token)
        if _SCRIPT_EXT_RE.search(clean):
            return basename(clean)
    match = _MODULE_RE.search(cmdline or "")
    if match:
        return match.group(1)
    return basename(exe) or "进程"


def script_dir_from_cmdline(cwd: str, cmdline: str) -> Optional[str]:
    tokens = split_cmdline(cmdline)
    for token in tokens[1:]:
        clean = strip_quotes(token)
        if not _SCRIPT_EXT_RE.search(clean):
            continue
        if re.match(r"^[a-zA-Z]:[\\/]", clean) or clean.startswith("\\\\"):
            return dirname(clean)
        if cwd:
            return dirname(os.path.normpath(os.path.join(cwd, clean)))
    return None


def resolve_folder(cwd: Optional[str], cmdline: str, exe: str) -> tuple[str, str]:
    """返回 (文件夹, 来源说明)。"""
    if cwd:
        return cwd.rstrip("\\/"), "工作目录"
    script_dir = script_dir_from_cmdline("", cmdline or "")
    if script_dir:
        return script_dir.rstrip("\\/"), "脚本目录"
    if cmdline:
        for token in split_cmdline(cmdline)[1:]:
            clean = strip_quotes(token)
            if re.match(r"^[a-zA-Z]:[\\/]", clean) and not _SCRIPT_EXT_RE.search(clean):
                candidate = dirname(clean)
                if candidate:
                    return candidate.rstrip("\\/"), "命令行参数"
    exe_dir = dirname(exe) if exe else ""
    return exe_dir.rstrip("\\/") or "未知", "可执行文件目录" if exe_dir else "未知"


def make_fingerprint(folder: str, cmdline: str, exe: str) -> str:
    title = experiment_title(cmdline or exe, exe)
    return f"{normalize_folder(folder)}|{title.lower()}"


def folder_display_name(folder: str) -> str:
    if not folder or folder == "未知":
        return "未知文件夹"
    norm = folder.rstrip("\\/")
    parts = [p for p in re.split(r"[\\/]", norm) if p]
    if not parts:
        return folder
    if len(parts) >= 2:
        if re.match(r"^[A-Za-z]:$", parts[-2]):
            return parts[-1]
        return "\\".join(parts[-2:])
    return parts[-1]


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@dataclass
class _Session:
    key: str
    root_pid: int
    create_time: float
    started_at: float
    cmdline: str
    exe: str
    name: str
    title: str
    folder: str
    folder_source: str
    fingerprint: str
    ancestor_cmdlines: list[str]
    miner: Optional[ProgressMiner]
    last_active: float
    members: list[int] = field(default_factory=list)
    cpu_pct: float = 0.0
    gpu_util: Optional[float] = 0.0
    gpu_mem_mb: Optional[float] = 0.0
    mem_mb: float = 0.0
    gpu_source: str = "none"
    progress: Optional[ProgressView] = None
    estimate: Optional[Estimate] = None
    hidden: bool = False
    kill_requested: bool = False
    # -- 暂停 -----------------------------------------------------------
    paused: bool = False
    paused_at: float = 0.0
    paused_total: float = 0.0
    paused_verify: int = 0
    pause_failed: str = ""

    # -- 有效运行时长（暂停期间不计入） --------------------------------
    def effective_elapsed(self, now: float) -> float:
        elapsed = now - self.started_at - self.paused_total
        if self.paused and self.paused_at:
            elapsed -= max(0.0, now - self.paused_at)
        return max(0.0, elapsed)

    def paused_seconds(self, now: float) -> float:
        if self.paused and self.paused_at:
            return max(0.0, now - self.paused_at)
        return 0.0


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------
class Tracker:
    def __init__(self, config: Config, history: HistoryStore,
                 descriptions: Optional[DescriptionStore] = None,
                 pauses: Optional[PauseRegistry] = None):
        self.config = config
        self.history = history
        base_dir = Path(config.path).parent
        self.descriptions = descriptions or DescriptionStore(base_dir / "descriptions.json")
        self.pauses = pauses or PauseRegistry(base_dir / "paused.json")
        self.sessions: dict[str, _Session] = {}
        self.prev_cpu: dict[tuple[int, float], tuple[float, float]] = {}
        self._streak: dict[tuple[int, float], int] = {}
        self._stats_cache: dict[str, tuple[int, HistoryStats]] = {}
        self.ncpu = winproc.cpu_count()
        self.session_id = winproc.current_session_id()
        self.self_pid = os.getpid()
        self._ignore_names = {
            str(item).strip().lower()
            for item in (config.get("detect.ignore_exe", []) or [])
            if str(item).strip()
        }
        self._ignore_cmd = [str(item).lower() for item in (config.get("detect.ignore_cmd_contains", []) or []) if str(item)]
        self._host_exes = {
            str(item).strip().lower()
            for item in (config.get("detect.host_exes", []) or [])
            if str(item).strip()
        }
        self._custom_rules = config.get("estimate.custom_progress_rules", []) or []
        self._tick = 0
        # 只排除小组件自身进程（按启动脚本 / 包名识别），不按项目目录排除
        self._self_markers = [
            "run_widget.py",
            "-m experiment_widget",
            "experiment_widget\\app.py",
            "experiment_widget\\__main__.py",
            "experiment_widget\\ui.py",
            "experiment_widget\\sampler.py",
        ]
        self._claimed_logs: dict[str, str] = {}  # 日志路径 -> 会话 key

    # -- 参数 -----------------------------------------------------------
    def _num(self, key: str, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    @property
    def min_cpu(self) -> float:
        return self._num("detect.min_cpu_pct", 2.0)

    @property
    def min_gpu(self) -> float:
        return self._num("detect.min_gpu_util", 3.0)

    @property
    def min_gpu_mem(self) -> float:
        return self._num("detect.min_gpu_mem_mb", 80.0)

    @property
    def unknown_gpu_mem(self) -> float:
        """未知程序仅凭显存识别实验时，要求更高的显存门槛，避免把桌面应用当实验。"""
        return self._num("detect.unknown_gpu_mem_mb", 512.0)

    @property
    def idle_hide_seconds(self) -> float:
        return self._num("detect.idle_hide_minutes", 90.0) * 60.0

    @property
    def history_enabled(self) -> bool:
        return bool(self.config.get("estimate.history_enabled", True))

    @property
    def history_min_seconds(self) -> float:
        return self._num("estimate.history_min_seconds", 45.0)

    @property
    def progress_enabled(self) -> bool:
        return bool(self.config.get("estimate.progress_enabled", True))

    # -- 主循环 ---------------------------------------------------------
    def update(self, procs: dict[int, ProcessStat], gpu: GpuSnapshot, now: float) -> Snapshot:
        self._tick += 1
        self._update_cpu(procs, now)
        system_cpu = winproc.system_cpu_pct()
        children = self._children_map(procs)

        self._remove_dead_sessions(procs, now)
        # 上次退出时仍处于暂停状态的实验：即使没有 CPU/GPU 活动也要恢复显示
        self._restore_paused_sessions(procs, children, gpu, now)
        new_roots = self._find_new_roots(procs, gpu)
        for root in new_roots:
            self._start_session(root, procs, children, gpu, now)

        # 吸收：如果父进程成为新的实验根，把之前独立跟踪的子实验并入
        self._absorb_descendant_sessions(procs, children, now)

        self._refresh_sessions(procs, children, gpu, now)

        sys_view = SystemView(
            cpu_pct=system_cpu,
            gpu_util=gpu.device_util,
            gpu_count=len(gpu.devices),
            vram_used_mb=gpu.vram_used_mb,
            vram_total_mb=gpu.vram_total_mb,
        )
        try:
            ram_used, ram_total = winproc.memory_info()
            sys_view.ram_used_mb = ram_used
            sys_view.ram_total_mb = ram_total
        except Exception:
            pass
        if not gpu.nvidia_ok and not gpu.windows_ok:
            sys_view.gpu_error = gpu.error or "无法读取 GPU 数据"

        folders, visible, hidden, paused = self._build_views(now)
        return Snapshot(
            ts=now,
            system=sys_view,
            folders=folders,
            total_experiments=visible,
            hidden_experiments=hidden,
            paused_experiments=paused,
            history_count=self.history.count,
        )

    # -- CPU 差值 -------------------------------------------------------
    def _update_cpu(self, procs: dict[int, ProcessStat], now: float) -> None:
        new_prev: dict[tuple[int, float], tuple[float, float]] = {}
        for proc in procs.values():
            prev = self.prev_cpu.get(proc.ident)
            pct = 0.0
            if prev is not None:
                dt = now - prev[0]
                if dt > 0.05:
                    pct = (proc.cpu_time - prev[1]) / dt / self.ncpu * 100.0
            proc.cpu_pct = max(0.0, min(100.0, pct))
            new_prev[proc.ident] = (now, proc.cpu_time)
        self.prev_cpu = new_prev

    @staticmethod
    def _children_map(procs: dict[int, ProcessStat]) -> dict[int, list[int]]:
        children: dict[int, list[int]] = {}
        for proc in procs.values():
            children.setdefault(proc.ppid, []).append(proc.pid)
        return children

    def _descendants(self, pid: int, children: dict[int, list[int]], limit: int = 400) -> list[int]:
        result: list[int] = []
        stack = list(children.get(pid, []))
        while stack and len(result) < limit:
            current = stack.pop()
            result.append(current)
            stack.extend(children.get(current, []))
        return result

    def _ancestors(self, pid: int, procs: dict[int, ProcessStat], limit: int = 12) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        current = procs.get(pid)
        while current and current.ppid and current.ppid not in seen and len(result) < limit:
            seen.add(current.ppid)
            result.append(current.ppid)
            current = procs.get(current.ppid)
        return result

    # -- 忽略判断 -------------------------------------------------------
    def _ignored_by_name(self, proc: ProcessStat) -> bool:
        names = {proc.name.lower(), basename(proc.exe).lower()}
        return bool(names & self._ignore_names)

    def _ignored_by_cmd(self, cmdline: str) -> bool:
        lowered = (cmdline or "").lower().replace("/", "\\")
        for marker in self._self_markers:
            if marker in lowered:
                return True
        for needle in self._ignore_cmd:
            if needle in lowered:
                return True
        return False

    def _cmdline(self, proc: ProcessStat, procs: Optional[dict[int, ProcessStat]] = None) -> str:
        line = winproc.get_command_line(proc.pid, proc.create_time)
        if line:
            return line
        # 少数进程读不到命令行，用父进程 & exe 兜底
        return proc.exe or proc.name

    # -- 新实验识别 -----------------------------------------------------
    def _find_new_roots(self, procs: dict[int, ProcessStat], gpu: GpuSnapshot) -> list[ProcessStat]:
        grace = max(1, int(self._num("detect.startup_grace_ticks", 2)))
        tracked_roots = {sess.root_pid for sess in self.sessions.values()}
        new_streak: dict[tuple[int, float], int] = {}
        ordered = sorted(procs.values(), key=lambda p: (p.create_time, p.pid))
        for proc in ordered:
            if proc.pid == self.self_pid:
                continue
            gpu_util = gpu.per_pid_util.get(proc.pid)
            gpu_mem = gpu.per_pid_mem_mb.get(proc.pid)
            active = (
                proc.cpu_pct >= self.min_cpu
                or (gpu_util is not None and gpu_util >= self.min_gpu)
                or (gpu_mem is not None and gpu_mem >= self.min_gpu_mem)
            )
            if not active:
                continue
            if self._ignored_by_name(proc):
                continue
            is_host = (proc.name.lower() in self._host_exes
                       or basename(proc.exe).lower() in self._host_exes)
            if is_host:
                gpu_ok = ((gpu_util or 0.0) >= self.min_gpu
                          or (gpu_mem or 0.0) >= self.min_gpu_mem)
            else:
                # 非已知实验宿主：需要有明显的 GPU 活动（避免把常驻显存的桌面应用当实验）
                gpu_ok = ((gpu_util or 0.0) >= self.min_gpu
                          or (gpu_mem or 0.0) >= self.unknown_gpu_mem)
            if not (is_host or gpu_ok):
                continue
            # 已经是某个被跟踪实验的子孙 → 归入该实验
            if proc.pid in tracked_roots:
                continue
            if self._has_tracked_ancestor(proc, procs, tracked_roots):
                continue
            cmdline = self._cmdline(proc)
            if self._ignored_by_cmd(cmdline):
                continue
            new_streak[proc.ident] = self._streak.get(proc.ident, 0) + 1

        self._streak = new_streak
        if not new_streak:
            return []
        # 只在连续活跃达到 grace 次后创建；并且只保留最上层的实验根
        accepted: list[ProcessStat] = []
        accepted_pids: set[int] = set()
        for proc in ordered:
            if proc.ident not in new_streak:
                continue
            if new_streak[proc.ident] < grace:
                continue
            if any(ancestor in accepted_pids or ancestor in tracked_roots
                   for ancestor in self._ancestors(proc.pid, procs)):
                continue
            accepted.append(proc)
            accepted_pids.add(proc.pid)
        return accepted

    def _has_tracked_ancestor(self, proc: ProcessStat, procs: dict[int, ProcessStat],
                              tracked_roots: set[int]) -> bool:
        return any(ancestor in tracked_roots for ancestor in self._ancestors(proc.pid, procs))

    def _start_session(self, proc: ProcessStat, procs: dict[int, ProcessStat],
                       children: dict[int, list[int]], gpu: GpuSnapshot, now: float,
                       paused_entry: Optional[dict] = None) -> None:
        cmdline = self._cmdline(proc)
        cwd = winproc.get_cwd(proc.pid, proc.create_time)
        folder, folder_source = resolve_folder(cwd, cmdline, proc.exe)
        fingerprint = make_fingerprint(folder, cmdline, proc.exe)
        title = experiment_title(cmdline, proc.exe)
        started_at = proc.create_time
        if paused_entry:
            # 使用上次保存的元数据兜底（暂停时仍可读取进程信息，但避免意外）
            cmdline = paused_entry.get("cmdline") or cmdline
            folder = paused_entry.get("folder") or folder
            fingerprint = paused_entry.get("fingerprint") or fingerprint
            title = paused_entry.get("title") or title
            folder_source = paused_entry.get("folder_source") or folder_source
            try:
                started_at = float(paused_entry.get("started_at") or started_at)
            except (TypeError, ValueError):
                pass
        key = f"{proc.pid}@{proc.create_time:.3f}"
        ancestors = find_ancestor_cmdlines(procs, proc.pid)
        override = (self.config.get("log_overrides", {}) or {}).get(fingerprint)
        miner = None
        if self.progress_enabled:
            miner = ProgressMiner(self.config, started_at, override=override,
                                  custom_rules=self._custom_rules)
        session = _Session(
            key=key,
            root_pid=proc.pid,
            create_time=proc.create_time,
            started_at=started_at,
            cmdline=cmdline,
            exe=proc.exe,
            name=proc.name,
            title=title,
            folder=folder,
            folder_source=folder_source,
            fingerprint=fingerprint,
            ancestor_cmdlines=ancestors,
            miner=miner,
            last_active=now,
        )
        if paused_entry:
            session.paused = True
            session.paused_at = now
            try:
                session.paused_total = float(paused_entry.get("paused_total") or 0.0)
            except (TypeError, ValueError):
                session.paused_total = 0.0
        self.sessions[key] = session
        log_line(f"new experiment: {title} pid={proc.pid} folder={folder!r} "
                 f"fp={fingerprint} paused={session.paused}")

    def _restore_paused_sessions(self, procs: dict[int, ProcessStat],
                                 children: dict[int, list[int]],
                                 gpu: GpuSnapshot, now: float) -> None:
        for entry in self.pauses.all():
            key = str(entry.get("key") or "")
            if not key or key in self.sessions:
                continue
            try:
                pid = int(entry.get("pid"))
                create_time = float(entry.get("create_time"))
            except (TypeError, ValueError):
                self.pauses.remove(key)
                continue
            proc = procs.get(pid)
            if proc is None or abs(proc.create_time - create_time) > 1.5:
                self.pauses.remove(key)
                continue
            self._start_session(proc, procs, children, gpu, now, paused_entry=entry)
            # 保留原始 key（与 registry 一致）
            session = self.sessions.pop(f"{proc.pid}@{proc.create_time:.3f}", None)
            if session is not None:
                session.key = key
                self.sessions[key] = session

    def _absorb_descendant_sessions(self, procs: dict[int, ProcessStat],
                                    children: dict[int, list[int]], now: float) -> None:
        roots = {sess.root_pid: sess for sess in self.sessions.values()}
        for session in list(self.sessions.values()):
            if session.root_pid not in procs:
                continue
            descendants = set(self._descendants(session.root_pid, children))
            for key, other in list(self.sessions.items()):
                if other is session:
                    continue
                if other.root_pid in descendants:
                    self.sessions.pop(key, None)
                    log_line(f"absorb session pid={other.root_pid} into pid={session.root_pid}")

    # -- 会话刷新 -------------------------------------------------------
    def _remove_dead_sessions(self, procs: dict[int, ProcessStat], now: float) -> None:
        for key, session in list(self.sessions.items()):
            proc = procs.get(session.root_pid)
            alive = proc is not None and abs(proc.create_time - session.create_time) < 1.5
            if alive:
                continue
            self.sessions.pop(key, None)
            if session.paused:
                self.pauses.remove(key)
            for path, owner in list(self._claimed_logs.items()):
                if owner == key:
                    self._claimed_logs.pop(path, None)
            if session.kill_requested:
                log_line(f"session killed: {session.title}")
                continue
            if session.paused:
                # 暂停期间被外部结束：状态不完整，不写入历史，避免污染 ETA
                log_line(f"session ended while paused: {session.title}")
                continue
            duration = session.effective_elapsed(now)
            if self.history_enabled and duration >= self.history_min_seconds:
                self.history.add(
                    fingerprint=session.fingerprint,
                    label=session.title,
                    folder=session.folder,
                    cmdline=session.cmdline,
                    seconds=duration,
                    started_at=session.started_at,
                    ended_at=now,
                )
                log_line(f"history record: {session.title} {duration:.0f}s")
            else:
                log_line(f"session ended (short): {session.title} {duration:.0f}s")

    def _refresh_sessions(self, procs: dict[int, ProcessStat],
                          children: dict[int, list[int]], gpu: GpuSnapshot,
                          now: float) -> None:
        gpu_known = bool(gpu.nvidia_ok or gpu.windows_ok)
        hide_seconds = self.idle_hide_seconds
        for session in self.sessions.values():
            members = [session.root_pid] + self._descendants(session.root_pid, children)
            # 去重并只保留存活进程
            members = [pid for pid in dict.fromkeys(members) if pid in procs]
            session.members = members
            cpu = 0.0
            gpu_util: Optional[float] = 0.0 if gpu_known else None
            gpu_mem: Optional[float] = 0.0 if gpu_known else None
            mem_mb = 0.0
            sources: set[str] = set()
            for pid in members:
                proc = procs[pid]
                cpu += proc.cpu_pct
                mem_mb += proc.mem_rss / 1048576.0
                if gpu_known:
                    util = gpu.per_pid_util.get(pid, 0.0)
                    mem = gpu.per_pid_mem_mb.get(pid, 0.0)
                    gpu_util = (gpu_util or 0.0) + util
                    gpu_mem = (gpu_mem or 0.0) + mem
                    source = gpu.util_source.get(pid)
                    if source:
                        sources.add(source)
            session.cpu_pct = max(0.0, min(100.0, cpu))
            session.mem_mb = mem_mb
            session.gpu_util = min(100.0, gpu_util) if gpu_util is not None else None
            session.gpu_mem_mb = gpu_mem
            session.gpu_source = ("pmon" if "pmon" in sources
                                  else "engine" if "engine" in sources else "none")
            active = session.cpu_pct >= 0.5 or (session.gpu_util or 0.0) >= 1.0
            if session.paused:
                # 万一被外部工具恢复运行，自动解除暂停状态
                if active:
                    session.paused_verify += 1
                    if session.paused_verify >= 2:
                        self._auto_unpause(session, now)
                else:
                    session.paused_verify = 0
                session.hidden = False
                session.last_active = now
            else:
                if active:
                    session.last_active = now
                idle_seconds = max(0.0, now - session.last_active)
                session.hidden = hide_seconds > 0 and idle_seconds > hide_seconds
            # 进程刚启动时可能暂时读不到命令行：后续补全元数据
            self._refresh_session_meta(session, procs)
            # 进度日志（暂停时不读日志、不更新估算）
            if session.paused:
                manual_total = self._manual_total(session.fingerprint)
                stats = self._history_stats(session.fingerprint)
                session.estimate = self._estimate(session, now, manual_total, stats)
                continue
            if self.progress_enabled:
                if session.miner is None:
                    override = (self.config.get("log_overrides", {}) or {}).get(
                        session.fingerprint)
                    session.miner = ProgressMiner(
                        self.config, session.started_at, override=override,
                        custom_rules=self._custom_rules)
                claimed = {path for path, owner in self._claimed_logs.items()
                           if owner != session.key}
                session.progress = session.miner.update(
                    now, session.folder, session.cmdline, session.ancestor_cmdlines,
                    exclude=claimed)
                # 登记 / 更新日志占用，避免两个实验绑定同一份日志
                current_path = (session.miner.state.path or "").lower()
                for path, owner in list(self._claimed_logs.items()):
                    if owner == session.key and path != current_path:
                        self._claimed_logs.pop(path, None)
                if current_path:
                    self._claimed_logs[current_path] = session.key
            else:
                session.progress = None
            manual_total = self._manual_total(session.fingerprint)
            stats = self._history_stats(session.fingerprint)
            session.estimate = self._estimate(session, now, manual_total, stats)

    def _refresh_session_meta(self, session: _Session,
                              procs: dict[int, ProcessStat]) -> None:
        if session.cmdline and session.cmdline != session.exe:
            return
        stat = procs.get(session.root_pid)
        if stat is None:
            return
        line = winproc.get_command_line(session.root_pid, session.create_time)
        if not line or line == session.exe:
            return
        session.cmdline = line
        session.title = experiment_title(line, session.exe)
        if session.folder_source in ("可执行文件目录", "未知"):
            cwd = winproc.get_cwd(session.root_pid, session.create_time)
            folder, source = resolve_folder(cwd, line, session.exe)
            session.folder = folder
            session.folder_source = source
        new_fingerprint = make_fingerprint(session.folder, line, session.exe)
        if new_fingerprint != session.fingerprint:
            session.fingerprint = new_fingerprint
            if session.miner is not None:
                override = (self.config.get("log_overrides", {}) or {}).get(
                    new_fingerprint)
                session.miner = ProgressMiner(
                    self.config, session.started_at, override=override,
                    custom_rules=self._custom_rules)
        log_line(f"session meta updated: pid={session.root_pid} title={session.title}")

    def _manual_total(self, fingerprint: str) -> Optional[float]:
        manual = (self.config.get("manual_estimates", {}) or {}).get(fingerprint)
        try:
            return float(manual) if manual else None
        except (TypeError, ValueError):
            return None

    def _history_stats(self, fingerprint: str) -> HistoryStats:
        revision = self.history.revision
        cached = self._stats_cache.get(fingerprint)
        if cached and cached[0] == revision:
            return cached[1]
        stats = self.history.stats(fingerprint)
        self._stats_cache[fingerprint] = (revision, stats)
        return stats

    def _estimate(self, session: _Session, now: float, manual_total: Optional[float],
                  stats: HistoryStats) -> Estimate:
        elapsed = session.effective_elapsed(now)
        progress = session.progress
        if progress is not None and progress.eta_seconds is not None and progress.eta_seconds >= 0:
            eta = progress.eta_seconds
            if progress.tqdm_eta is not None:
                basis, basis_label = "tqdm", "进度条"
            else:
                basis, basis_label = "progress", "进度"
            confidence = "high" if not progress.stale else "medium"
            if progress.rate is None and progress.tqdm_eta is None:
                confidence = "low"
            detail = f"{progress.source} {progress.label}"
            if progress.stale:
                detail += "（日志已一段时间未更新）"
            return Estimate(
                seconds=max(0.0, eta),
                basis=basis,
                confidence=confidence,
                detail=detail,
                total_seconds=elapsed + max(0.0, eta),
                progress_pct=progress.pct,
                basis_label=basis_label,
            )
        if manual_total and manual_total > 0:
            remaining = max(0.0, manual_total - elapsed)
            return Estimate(
                seconds=remaining,
                basis="manual",
                confidence="high",
                detail=f"手动设定总时长 {fmt_elapsed_long(manual_total)}",
                total_seconds=manual_total,
                progress_pct=min(100.0, elapsed / manual_total * 100.0),
                basis_label="手动",
            )
        if stats.count and stats.median:
            remaining = max(0.0, stats.median - elapsed)
            detail = f"历史中位时长 {fmt_elapsed_long(stats.median)}（{stats.count} 次）"
            if remaining <= 0:
                detail += "，已超过历史时长"
            return Estimate(
                seconds=remaining,
                basis="history",
                confidence="high" if stats.count >= 3 else "medium",
                detail=detail,
                total_seconds=stats.median,
                progress_pct=min(100.0, elapsed / stats.median * 100.0),
                basis_label="历史",
            )
        # 4) 有进度但还没算出瞬时速率：用“启动以来平均速度”粗估
        if progress is not None and progress.pct > 0.5 and elapsed >= 60:
            remaining: Optional[float] = None
            if (progress.kind == "fraction" and progress.current is not None
                    and progress.total):
                average_rate = progress.current / elapsed
                if average_rate > 0:
                    remaining = max(0.0, (progress.total - progress.current) / average_rate)
            else:
                average_rate = progress.pct / elapsed
                if average_rate > 0:
                    remaining = max(0.0, (100.0 - progress.pct) / average_rate)
            if remaining is not None and remaining < 30 * 86400:
                detail = f"{progress.source} {progress.label}（按启动以来平均速度推算）"
                return Estimate(
                    seconds=remaining,
                    basis="progress",
                    confidence="low",
                    detail=detail,
                    total_seconds=elapsed + remaining,
                    progress_pct=progress.pct,
                    basis_label="进度",
                )
        return Estimate(
            seconds=None,
            basis="none",
            confidence="none",
            detail=self._unknown_eta_detail(session),
            basis_label="未知",
        )

    @staticmethod
    def _unknown_eta_detail(session: _Session) -> str:
        """说明“为什么没法估算”，而不是只显示未知。"""
        miner = session.miner
        path = (miner.state.path if miner is not None else None) or None
        if path:
            name = os.path.basename(path)
            if not os.path.isfile(path):
                return (f"进度日志 {name} 还没生成；可右键指定日志，"
                        "或设置预计总时长")
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                age = 0.0
            return (f"已找到日志 {name}，但没有识别到进度（支持 tqdm / Epoch / "
                    f"step / 500/7442 / 百分比，日志已有 {fmt_age_minutes(age)} 没更新）；"
                    "可在右键指定其它日志，或设置预计总时长")
        tried = getattr(miner.state, "discover_attempts", 0) if miner is not None else 0
        prefix = f"已尝试 {tried} 次" if tried else "未找到"
        return (f"{prefix}可用的进度日志，也没有历史运行记录；"
                "右键可指定日志、设置预计总时长，或让实验输出进度日志")

    # -- 视图 -----------------------------------------------------------
    def _build_views(self, now: float) -> tuple[list[FolderView], int, int, int]:
        groups: dict[str, FolderView] = {}
        visible = 0
        hidden = 0
        paused = 0
        for session in sorted(self.sessions.values(), key=lambda s: s.started_at):
            if session.estimate is None:
                continue
            idle_seconds = max(0.0, now - session.last_active)
            desc = self.descriptions.resolve(
                session.fingerprint, session.folder, session.cmdline, session.exe)
            view = ExperimentView(
                key=session.key,
                fingerprint=session.fingerprint,
                name=session.title,
                cmdline=session.cmdline,
                folder=session.folder,
                folder_source=session.folder_source,
                exe=session.exe,
                pids=list(session.members or [session.root_pid]),
                started_at=session.started_at,
                elapsed=session.effective_elapsed(now),
                cpu_pct=session.cpu_pct,
                gpu_util=session.gpu_util,
                gpu_mem_mb=session.gpu_mem_mb,
                mem_mb=session.mem_mb,
                gpu_source=session.gpu_source,
                idle_seconds=idle_seconds,
                estimate=session.estimate,
                progress=session.progress,
                log_path=session.miner.state.path if session.miner else None,
                manual_total=self._manual_total(session.fingerprint),
                hidden=session.hidden,
                framework=desc.get("framework", ""),
                idea=desc.get("idea", ""),
                note=desc.get("note", ""),
                description_source=desc.get("description_source", ""),
                paused=session.paused,
                paused_seconds=session.paused_seconds(now),
            )
            if session.paused:
                paused += 1
            if view.hidden:
                hidden += 1
            else:
                visible += 1
            key = session.folder or "未知"
            group = groups.get(key)
            if group is None:
                group = FolderView(
                    folder=key,
                    display=folder_display_name(key),
                )
                groups[key] = group
            group.experiments.append(view)
        # 聚合文件夹指标
        for group in groups.values():
            shown = [exp for exp in group.experiments if not exp.hidden]
            if not shown:
                shown = group.experiments
            group.count = len(shown)
            group.cpu_pct = min(100.0, sum(exp.cpu_pct for exp in shown))
            utils = [exp.gpu_util for exp in shown if exp.gpu_util is not None]
            group.gpu_util = min(100.0, sum(utils)) if utils else None
            mems = [exp.gpu_mem_mb for exp in shown if exp.gpu_mem_mb is not None]
            group.gpu_mem_mb = sum(mems) if mems else None
        folders = sorted(groups.values(), key=lambda g: g.experiments[0].started_at if g.experiments else 0)
        return folders, visible, hidden, paused

    # -- 外部操作 -------------------------------------------------------
    def get_view(self, key: str) -> Optional[ExperimentView]:
        session = self.sessions.get(key)
        if not session or session.estimate is None:
            return None
        now = time.time()
        idle_seconds = max(0.0, now - session.last_active)
        desc = self.descriptions.resolve(
            session.fingerprint, session.folder, session.cmdline, session.exe)
        return ExperimentView(
            key=session.key,
            fingerprint=session.fingerprint,
            name=session.title,
            cmdline=session.cmdline,
            folder=session.folder,
            folder_source=session.folder_source,
            exe=session.exe,
            pids=list(session.members or [session.root_pid]),
            started_at=session.started_at,
            elapsed=session.effective_elapsed(now),
            cpu_pct=session.cpu_pct,
            gpu_util=session.gpu_util,
            gpu_mem_mb=session.gpu_mem_mb,
            mem_mb=session.mem_mb,
            gpu_source=session.gpu_source,
            idle_seconds=idle_seconds,
            estimate=session.estimate,
            progress=session.progress,
            log_path=session.miner.state.path if session.miner else None,
            manual_total=self._manual_total(session.fingerprint),
            hidden=session.hidden,
            framework=desc.get("framework", ""),
            idea=desc.get("idea", ""),
            note=desc.get("note", ""),
            description_source=desc.get("description_source", ""),
            paused=session.paused,
            paused_seconds=session.paused_seconds(now),
        )

    # -- 暂停 / 继续 -----------------------------------------------------
    def pause(self, key: str, now: Optional[float] = None) -> dict:
        """安全暂停：冻结整棵进程树，状态保留在内存中，可立即继续。"""
        session = self.sessions.get(key)
        if session is None:
            return {"ok": False, "message": "找不到该实验（可能已结束）", "count": 0}
        now = time.time() if now is None else now
        if session.paused:
            return {"ok": True, "message": "实验已经处于暂停状态", "count": 0}
        # 用最新快照重新收集整棵进程树，避免漏掉刚刚 spawn 的 worker
        fresh = None
        try:
            fresh = winproc.snapshot_processes(only_session=self.session_id)
            children = self._children_map(fresh)
            members = [session.root_pid] + self._descendants(session.root_pid, children)
        except Exception:
            members = list(session.members or [session.root_pid])
        if fresh is not None:
            members = [pid for pid in dict.fromkeys(members) if pid in fresh]
        else:
            members = list(dict.fromkeys(members))
        if session.root_pid not in members:
            members.insert(0, session.root_pid)
        session.members = members
        ordered = [session.root_pid] + [pid for pid in members if pid != session.root_pid]
        suspended, failed = winproc.suspend_processes(ordered)
        if session.root_pid not in suspended:
            if suspended:
                winproc.resume_processes(suspended)
            return {"ok": False,
                    "message": "暂停失败：进程可能已退出，或权限不足（需与实验同一用户）",
                    "count": 0}
        session.paused = True
        session.paused_at = now
        session.paused_verify = 0
        session.pause_failed = ""
        session.last_active = now
        self.pauses.add(session.key, {
            "key": session.key,
            "pid": session.root_pid,
            "create_time": session.create_time,
            "started_at": session.started_at,
            "paused_total": session.paused_total,
            "fingerprint": session.fingerprint,
            "folder": session.folder,
            "folder_source": session.folder_source,
            "title": session.title,
            "cmdline": session.cmdline,
            "exe": session.exe,
        })
        log_line(f"paused {session.title} members={suspended} failed={failed}")
        message = f"已暂停 {len(suspended)} 个进程，可随时继续"
        if failed:
            message += f"（{len(failed)} 个子进程未能暂停）"
        return {"ok": True, "message": message, "count": len(suspended),
                "failed": failed}

    def resume(self, key: str, now: Optional[float] = None) -> dict:
        session = self.sessions.get(key)
        if session is None:
            return {"ok": False, "message": "找不到该实验（可能已结束）", "count": 0}
        now = time.time() if now is None else now
        if not session.paused:
            return {"ok": True, "message": "实验并未暂停", "count": 0}
        members = [pid for pid in (session.members or [session.root_pid]) if pid]
        resumed, failed = winproc.resume_processes(members)
        if session.root_pid not in resumed:
            return {"ok": False,
                    "message": "继续失败：进程可能已被外部结束，或权限不足",
                    "count": len(resumed)}
        session.paused_total += max(0.0, now - session.paused_at)
        session.paused = False
        session.paused_at = 0.0
        session.paused_verify = 0
        session.last_active = now
        self.pauses.remove(session.key)
        log_line(f"resumed {session.title} members={resumed}")
        message = f"已继续运行（{len(resumed)} 个进程）"
        if failed:
            message += f"（{len(failed)} 个子进程未恢复）"
        return {"ok": True, "message": message, "count": len(resumed)}

    def _auto_unpause(self, session: _Session, now: float) -> None:
        """检测到暂停中的进程重新有活动 → 认为被外部工具恢复。"""
        if session.paused_at:
            session.paused_total += max(0.0, now - session.paused_at)
        session.paused = False
        session.paused_at = 0.0
        session.paused_verify = 0
        session.last_active = now
        self.pauses.remove(session.key)
        log_line(f"external resume detected: {session.title}")

    def paused_sessions(self) -> list[_Session]:
        return [session for session in self.sessions.values() if session.paused]

    # -- 实验描述 -------------------------------------------------------
    def set_description(self, fingerprint: str, idea: str, framework: str,
                        note: str) -> None:
        self.descriptions.set(fingerprint, idea, framework, note)

    def clear_description(self, fingerprint: str) -> None:
        self.descriptions.clear(fingerprint)

    def terminate(self, key: str) -> list[int]:
        session = self.sessions.get(key)
        if not session:
            return []
        session.kill_requested = True
        if session.paused:
            self.pauses.remove(session.key)
        procs = winproc.snapshot_processes(only_session=self.session_id)
        return winproc.terminate_process_tree(session.root_pid, procs=procs)

    def set_manual_estimate(self, fingerprint: str, seconds: Optional[float]) -> None:
        data = self.config.data.setdefault("manual_estimates", {})
        if seconds is None:
            data.pop(fingerprint, None)
        else:
            data[fingerprint] = float(seconds)
        self.config.save()

    def set_log_override(self, fingerprint: str, path: Optional[str]) -> None:
        data = self.config.data.setdefault("log_overrides", {})
        if path:
            data[fingerprint] = path
        else:
            data.pop(fingerprint, None)
        self.config.save()
        for session in self.sessions.values():
            if session.fingerprint == fingerprint and session.miner:
                session.miner.set_override(path)

    def clear_history(self) -> None:
        self.history.clear()
        self._stats_cache.clear()
