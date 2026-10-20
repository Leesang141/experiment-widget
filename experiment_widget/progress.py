"""进度日志发现与进度解析（用于估算剩余时间）。

支持：
- tqdm 风格进度条（``45%|████     | 450/1000 [01:23<02:34, 6.1it/s]``）；
- ``Epoch 3/50``、``step 1200/5000`` 等带总量字段；
- 单独成行的 ``500/7442`` 计数；
- 百分比进度条（``[#####   ] 45%``）；
- 配置中的自定义正则（``estimate.custom_progress_rules``）。

日志文件定位：
1. 命令行（含父进程命令行）里的重定向 / ``--log-file``；
2. 在实验工作目录、脚本目录及其父目录中寻找最近被写入的 ``*.log/.txt/.out``，
   结合文件名相似度挑选；
3. 用户手动指定（右键菜单）。
"""

from __future__ import annotations

import glob
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import ProgressView
from .util import basename, dirname, log_line, similarity, strip_quotes

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
_TQDM_RE = re.compile(
    r"(?P<pct>\d{1,3})%\|[^|\r\n]*\|\s*(?P<cur>\d+)\s*/\s*(?P<total>\d+)\s*"
    r"\[(?P<elapsed>[^\]<]*)<(?P<remain>[^,\]]+)(?:,\s*(?P<rate>[^\]]*))?\]"
)
_TQDM_PCT_RE = re.compile(
    r"(?P<pct>\d{1,3})%\|[^|\r\n]*\|[^\r\n]*\[(?P<elapsed>[^\]<]*)<(?P<remain>[^,\]]+)"
)
_NAMED_RE = re.compile(
    r"(?i)\b(?P<name>epoch|step|iter(?:ation)?|batch|fold|seed|round)\s*[:=#\s]*?"
    r"(?P<cur>\d{1,9})\s*(?:/|of)\s*(?P<total>\d{1,9})"
)
_BARE_FRACTION_RE = re.compile(
    r"^\s*[^\d\r\n]{0,12}(?P<cur>\d{1,9})\s*/\s*(?P<total>\d{1,9})\s*(?:$|[\s\]\),;])"
)
_BAR_PCT_RE = re.compile(r"[\[\(]?[#=>.\-\s█▓▒░▏▎▍▌▋▊▉]{5,}[\]\)]?\s*(?P<pct>\d{1,3}(?:\.\d+)?)\s*%")
_PCT_RE = re.compile(r"(?P<pct>\d{1,3}(?:\.\d+)?)\s*%")
_PCT_HINT = re.compile(r"(?i)(progress|epoch|step|iter|batch|fold|train|test|eval|val|done|完成|进度)")

_REDIRECT_RE = re.compile(r"(?:^|[\s&|;(])>>?\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s&|<>]+))")
_LOGOPT_RE = re.compile(
    r"(?i)--(?:log[-_]?file|logfile|log|output[-_]?log|stdout[-_]?log)[=\s]+"
    r"(?:\"([^\"]+)\"|'([^']+)'|([^\s&|;]+))"
)
_TEE_RE = re.compile(r"(?:^|[\s&|;(])tee\s+(?:-a\s+)?(?:\"([^\"]+)\"|'([^']+)'|([^\s&|;]+))")

_LOG_EXTS = {".log", ".txt", ".out", ".stdout", ".stderr", ".err"}
_SKIP_DIRS = {
    ".git", ".svn", ".hg", ".venv", "venv", "env", ".env", "node_modules",
    "__pycache__", ".idea", ".vscode", ".conda", "site-packages", ".cache",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".ipynb_checkpoints",
    "dist-info", ".tox", ".next", ".nuxt", "build", "dist",
}
_SCAN_FILE_LIMIT = 6000
_SCAN_TIME_LIMIT = 0.9


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ParsedProgress:
    cur: Optional[float]
    total: Optional[float]
    pct: Optional[float]
    kind: str  # fraction / percent
    source: str
    line: str
    tqdm_eta: Optional[float] = None
    priority: int = 3  # 0 最高
    display_label: Optional[str] = None


@dataclass
class ProgressSample:
    ts: float
    value: float  # cur（fraction）或 pct（percent）


@dataclass
class ProgressState:
    path: Optional[str] = None
    direct: bool = False  # 来自命令行重定向 / 用户手动指定，可信度最高
    rejected: set[str] = field(default_factory=set)
    last_size: int = -1
    last_mtime: float = 0.0
    last_read_ts: float = 0.0
    last_parse_ts: float = 0.0
    samples: deque = field(default_factory=lambda: deque(maxlen=60))
    last: Optional[ParsedProgress] = None
    next_discover_ts: float = 0.0
    discover_attempts: int = 0
    parse_fail_ticks: int = 0
    override: Optional[str] = None


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _parse_tqdm_duration(text: str) -> Optional[float]:
    text = (text or "").strip()
    if not text or text in {"?", "-"}:
        return None
    match = re.match(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$", text)  # [h:]m:s
    if match:
        hours = int(match.group(1) or 0)
        return hours * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    match = re.match(r"^(\d+):(\d+(?:\.\d+)?)$", text)  # m:s
    if match:
        return int(match.group(1)) * 60 + float(match.group(2))
    match = re.match(r"^(\d+(?:\.\d+)?)(s|m|h)$", text)
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        return value * {"s": 1, "m": 60, "h": 3600}[unit]
    return None


def _valid_fraction(cur: float, total: float) -> bool:
    return total > 0 and 0 <= cur <= total and total >= 5


def _parse_line(line: str, custom_rules: list) -> Optional[ParsedProgress]:
    line = line.strip("\x00")
    stripped = line.strip()
    if not stripped:
        return None
    # 1) tqdm（带总量 + 自带剩余时间）
    match = _TQDM_RE.search(line)
    if match:
        cur = float(match.group("cur"))
        total = float(match.group("total"))
        if _valid_fraction(cur, total):
            return ParsedProgress(
                cur=cur, total=total, pct=100.0 * cur / total, kind="fraction",
                source="tqdm", line=stripped, priority=0,
                tqdm_eta=_parse_tqdm_duration(match.group("remain")),
            )
    match = _TQDM_PCT_RE.search(line)
    if match:
        pct = float(match.group("pct"))
        if 0 <= pct <= 100:
            return ParsedProgress(
                cur=None, total=None, pct=pct, kind="percent", source="tqdm",
                line=stripped, priority=0,
                tqdm_eta=_parse_tqdm_duration(match.group("remain")),
            )
    # 2) 自定义规则
    for rule in custom_rules or []:
        try:
            match = rule["regex"].search(line)
        except Exception:
            continue
        if not match:
            continue
        groups = match.groupdict()
        try:
            if groups.get("cur") is not None and groups.get("total") is not None:
                cur, total = float(groups["cur"]), float(groups["total"])
                if _valid_fraction(cur, total):
                    return ParsedProgress(cur=cur, total=total, pct=100.0 * cur / total,
                                          kind="fraction", source=rule.get("name", "自定义"),
                                          line=stripped, priority=0)
            elif groups.get("pct") is not None:
                pct = float(groups["pct"])
                if 0 <= pct <= 100:
                    return ParsedProgress(cur=None, total=None, pct=pct, kind="percent",
                                          source=rule.get("name", "自定义"), line=stripped,
                                          priority=0)
        except (TypeError, ValueError):
            continue
    # 3) Epoch / Step 12/50
    match = _NAMED_RE.search(line)
    if match:
        cur = float(match.group("cur"))
        total = float(match.group("total"))
        if _valid_fraction(cur, total):
            return ParsedProgress(cur=cur, total=total, pct=100.0 * cur / total,
                                  kind="fraction", source=match.group("name").lower(),
                                  line=stripped, priority=1)
    # 4) 单独成行的 500/7442
    match = _BARE_FRACTION_RE.match(line)
    if match:
        cur = float(match.group("cur"))
        total = float(match.group("total"))
        if _valid_fraction(cur, total) and total >= 20:
            return ParsedProgress(cur=cur, total=total, pct=100.0 * cur / total,
                                  kind="fraction", source="计数", line=stripped, priority=2)
    # 5) 百分比进度条 / 带上下文的百分比
    match = _BAR_PCT_RE.search(line)
    if match:
        pct = float(match.group("pct"))
        if 0 <= pct <= 100:
            return ParsedProgress(cur=None, total=None, pct=pct, kind="percent",
                                  source="进度条", line=stripped, priority=3)
    match = _PCT_RE.search(line)
    if match and (_PCT_HINT.search(line) or any(ch in line for ch in "█▓▒░|")):
        pct = float(match.group("pct"))
        if 0 <= pct <= 100:
            return ParsedProgress(cur=None, total=None, pct=pct, kind="percent",
                                  source="百分比", line=stripped, priority=3)
    return None


def parse_tail_text(text: str, custom_rules: Optional[list] = None) -> Optional[ParsedProgress]:
    """从日志尾部文本中找出最新一条可识别的进度。优先级由高到低。"""
    if not text:
        return None
    lines = [ln for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    lines = lines[-120:]
    tiers: list[list[ParsedProgress]] = [[], [], [], []]
    for line in lines:
        parsed = _parse_line(line, custom_rules or [])
        if parsed:
            tiers[min(3, parsed.priority)].append(parsed)
    for tier in tiers:
        if tier:
            return tier[-1]
    return None


# ---------------------------------------------------------------------------
# 日志发现
# ---------------------------------------------------------------------------
_scan_cache: dict[tuple[str, int], tuple[float, list[tuple[str, float, int]]]] = {}


_NON_LOG_EXTS = {".py", ".pyw", ".ipynb", ".sh", ".ps1", ".js", ".ts", ".jl",
                 ".r", ".m", ".cpp", ".cc", ".c", ".h", ".cu", ".rs", ".go",
                 ".java", ".exe", ".dll", ".bat", ".cmd"}


def _resolve(base: str, path: str) -> Optional[str]:
    path = strip_quotes(path).strip().strip("'\"")
    # 处理 \\"C:\... 之类的转义/残留引号；并取最后一个盘符路径
    path = path.replace('\\"', '"').replace('"', "").replace("'", "")
    # Git Bash 风格路径：/c/Users/... /d/项目/... /tmp/xxx
    match = re.match(r"^/([a-zA-Z])/(.*)$", path)
    if match:
        path = f"{match.group(1).upper()}:\\{match.group(2).replace('/', chr(92))}"
    elif path.lower().startswith("/tmp/"):
        temp = os.environ.get("TEMP") or os.environ.get("TMP") or r"C:\Windows\Temp"
        path = os.path.join(temp, path[5:].replace("/", "\\"))
    matches = list(re.finditer(r"[A-Za-z]:[\\/]", path))
    if matches:
        path = path[matches[-1].start():]
    if not path:
        return None
    if re.match(r"^[a-zA-Z]:[\\/]", path) or path.startswith("\\\\"):
        return path
    if path.startswith("~"):
        return os.path.expanduser(path)
    if base:
        return os.path.normpath(os.path.join(base, path.replace("/", "\\")))
    return None


def _expand_redirect_path(path: str) -> list[str]:
    """展开 shell 变量 / 通配符，例如 /tmp/kd5_$s.log → 真实存在的若干日志。"""
    if "$" not in path and "%" not in path and "*" not in path and "?" not in path:
        return [path]
    pattern = re.sub(r"\$\{[^}]+\}|\$[A-Za-z_]\w*|%[^%]+%", "*", path)
    try:
        matches = [item for item in glob.glob(pattern) if os.path.isfile(item)]
    except Exception:
        matches = []
    if matches:
        matches.sort(key=lambda item: os.path.getmtime(item), reverse=True)
    return matches


def _redirect_candidates(cwd: str, cmdlines: Iterable[str]) -> list[str]:
    found: list[str] = []
    for cmdline in cmdlines or []:
        if not cmdline:
            continue
        for regex in (_REDIRECT_RE, _LOGOPT_RE, _TEE_RE):
            for match in regex.finditer(cmdline):
                raw = next((g for g in match.groups() if g), None)
                if not raw:
                    continue
                if raw in ("/dev/null", "nul", "NUL"):
                    continue
                path = _resolve(cwd, raw)
                if not path:
                    continue
                for expanded in _expand_redirect_path(path):
                    if os.path.splitext(expanded)[1].lower() in _NON_LOG_EXTS:
                        continue
                    if expanded not in found:
                        found.append(expanded)
    return found


def _looks_like_noise_dir(path: str) -> bool:
    parts = {p.lower() for p in re.split(r"[\\/]", path) if p}
    return bool(parts & _SKIP_DIRS)


def _scan_dir(root: str, depth: int, since_ts: float) -> list[tuple[str, float, int]]:
    """在 root 下递归寻找近期的日志类文件（带缓存）。"""
    if not root or not os.path.isdir(root):
        return []
    cache_key = (root.lower(), depth)
    cached = _scan_cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < 30.0:
        return [(p, m, s) for (p, m, s) in cached[1] if m >= since_ts]
    results: list[tuple[str, float, int]] = []
    deadline = time.monotonic() + _SCAN_TIME_LIMIT
    stack: list[tuple[str, int]] = [(root, 0)]
    visited = 0
    while stack:
        current, level = stack.pop()
        if time.monotonic() > deadline or visited > _SCAN_FILE_LIMIT:
            break
        try:
            entries = list(os.scandir(current))
        except (PermissionError, OSError):
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if level < depth and entry.name.lower() not in _SKIP_DIRS:
                        stack.append((entry.path, level + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                visited += 1
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in _LOG_EXTS:
                    continue
                # 用 os.stat 强制取新的大小：Windows 目录枚举缓存可能滞后
                stat = os.stat(entry.path)
                if stat.st_size <= 0:
                    continue
                results.append((entry.path, stat.st_mtime, stat.st_size))
            except OSError:
                continue
    results.sort(key=lambda item: item[1], reverse=True)
    _scan_cache[cache_key] = (now, results)
    if len(_scan_cache) > 64:
        _scan_cache.clear()
    return [(p, m, s) for (p, m, s) in results if m >= since_ts]


_GENERIC_DIR_NAMES = {"appdata", "local", "roaming", "windows", "programdata",
                      "temp", "tmp", "users", "public", "system32"}


def _generic_scan_paths() -> set[str]:
    paths = {"c:\\users", "c:\\windows", "c:\\program files",
             "c:\\program files (x86)", "c:\\programdata"}
    for env in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "PUBLIC"):
        value = os.environ.get(env)
        if value:
            try:
                paths.add(os.path.normpath(value).lower())
            except Exception:
                pass
    return paths


def _scan_roots(cwd: str, cmdline: str) -> list[tuple[str, int]]:
    roots: list[tuple[str, int]] = []
    cwd = (cwd or "").rstrip("\\/")
    generic_paths = _generic_scan_paths()

    def add(path: Optional[str], depth: int) -> None:
        if not path:
            return
        try:
            path = os.path.normpath(os.path.abspath(path))
        except Exception:
            return
        if not os.path.isdir(path):
            return
        drive, tail = os.path.splitdrive(path)
        if tail in ("", "\\", "/"):
            return  # 盘符根目录 / "C:" 这种相对写法不扫描
        lowered = path.lower()
        if lowered in generic_paths:
            return
        # 跳过 AppData/Local/Temp 这类包含大量无关日志的目录
        if os.path.basename(lowered) in _GENERIC_DIR_NAMES:
            return
        if any(path.lower() == existing.lower() for existing, _ in roots):
            return
        roots.append((path, depth))

    add(cwd, 4)
    add(dirname(cwd), 3)
    add(dirname(dirname(cwd)), 3)
    # 命令行里的脚本路径也能提示项目位置（仅限绝对路径，避免误扫当前目录）
    for token in re.findall(r"[^\s\"']+\.(?:py|pyw|jl|sh|ps1|r|js|m)\b", cmdline or ""):
        resolved = _resolve(cwd, token)
        if resolved and os.path.isabs(resolved):
            script_dir = dirname(resolved)
            add(script_dir, 3)
            add(dirname(script_dir), 3)
    # 常见日志目录优先扫描，避免大项目里被文件数/时间上限截断
    for base, _depth in list(roots):
        for sub in ("logs", "log", "outputs", "output", "results"):
            add(os.path.join(base, sub), 2)
    return roots


_LOG_DIR_PARTS = {"logs", "log", "runs", "outputs", "output", "results", "result", "checkpoints"}


def _in_log_dir(path: str) -> bool:
    parts = {p.lower() for p in re.split(r"[\\/]", path) if p}
    return bool(parts & _LOG_DIR_PARTS)


def _score_candidate(path: str, mtime: float, size: int, cmdline: str, started_at: float,
                     parse_ok: bool, cwd: str = "", script_dir: str = "") -> float:
    score = 0.0
    stem = os.path.splitext(basename(path))[0]
    score += 8.0 * similarity(stem, cmdline)
    score += _proximity_bonus(path, cwd, script_dir)
    if _in_log_dir(path):
        score += 2.0
    if parse_ok:
        score += 6.0
    age = max(0.0, time.time() - mtime)
    if age < 60:
        score += 3.0
    elif age < 300:
        score += 2.0
    elif age < 1800:
        score += 1.0
    if started_at:
        if mtime >= started_at - 60:
            score += 3.0
        else:
            score -= 2.0
    if size > 5 * 1024 * 1024:
        score -= 1.0
    return score


def _is_relevant(path: str, cmdline: str, parse_ok: bool) -> bool:
    stem = os.path.splitext(basename(path))[0]
    return parse_ok or _in_log_dir(path) or similarity(stem, cmdline) >= 0.25


def _is_app_data(path: str) -> bool:
    try:
        from .config import DATA_DIR

        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(os.path.abspath(str(DATA_DIR))))
    except Exception:
        return False


_TOTAL_EPOCHS_CMD_RE = re.compile(
    r"(?i)--(?:epochs?|num[_-]?epochs|max[_-]?epochs|n[_-]?epochs)[=\s]+(\d+)")
_TOTAL_EPOCHS_LOG_RE = re.compile(r"(?i)\bepochs?\s*[=:]\s*(\d+)")
_EPOCH_NUM_RE = re.compile(r"(?i)\bepoch\s*[:=#]?\s*(\d{1,5})\b")


def extract_total_epochs(cmdline: str, text: str = "") -> Optional[int]:
    match = _TOTAL_EPOCHS_CMD_RE.search(cmdline or "")
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    match = _TOTAL_EPOCHS_LOG_RE.search(text or "")
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def latest_epoch(text: str) -> Optional[int]:
    matches = _EPOCH_NUM_RE.findall(text or "")
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def combine_epoch_progress(parsed: ParsedProgress, text: str,
                           total_epochs: Optional[int]) -> ParsedProgress:
    """把“epoch 内进度”换算成整个训练的进度。

    典型日志：``Epoch 65 ... 57%|... 4/7 [00:05<00:03]``，
    命令行里有 ``--epochs 80``。此时真正的总体进度是
    ``(65 - 1 + 4/7) / 80``，而不是 4/7、更不是 tqdm 的 3 秒剩余。
    """
    if not total_epochs or total_epochs <= 0:
        return parsed
    # 已经是“Epoch 65/80”这种整体进度，直接使用
    if (parsed.kind == "fraction" and parsed.total
            and abs(parsed.total - total_epochs) < 1e-6):
        return parsed
    epoch = latest_epoch(text)
    if epoch is None or not (1 <= epoch <= total_epochs):
        # 有总 epoch 但定位不到当前 epoch：忽略 epoch 内的 tqdm 剩余时间，
        # 避免把“本 epoch 还剩 3 秒”误报成整个实验的预计剩余时间。
        if parsed.tqdm_eta is not None:
            parsed = ParsedProgress(
                cur=parsed.cur, total=parsed.total, pct=parsed.pct,
                kind=parsed.kind, source=parsed.source + "（epoch 内）",
                line=parsed.line, priority=parsed.priority,
                tqdm_eta=None, display_label=None)
        return parsed
    inner = (parsed.pct or 0.0) / 100.0
    inner = max(0.0, min(1.0, inner))
    cur = (epoch - 1) + inner
    cur = min(float(total_epochs), cur)
    return ParsedProgress(
        cur=cur,
        total=float(total_epochs),
        pct=100.0 * cur / total_epochs,
        kind="fraction",
        source=f"{parsed.source}+epoch",
        line=parsed.line,
        priority=parsed.priority,
        tqdm_eta=None,
        display_label=f"Epoch {epoch}/{total_epochs} · {inner * 100:.0f}%",
    )


def _extract_seed(cmdline: str) -> Optional[int]:
    match = re.search(r"(?i)--seed(?:_base)?[=\s]+(\d+)", cmdline or "")
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def _script_stem(cmdline: str) -> str:
    for token in re.findall(r"[^\s\"']+\.(?:py|pyw|jl|sh|r|js|m)\b", cmdline or ""):
        stem = os.path.splitext(basename(token))[0]
        if stem:
            return stem
    return ""


def _script_dir(cmdline: str) -> str:
    for token in re.findall(r"[^\s\"']+\.(?:py|pyw|jl|sh|r|js|m)\b", cmdline or ""):
        token = token.strip("\"'")
        if os.path.isabs(token) or re.match(r"^[a-zA-Z]:[\\/]", token):
            return dirname(token)
    return ""


def _proximity_bonus(path: str, cwd: str, script_dir: str) -> float:
    """日志离实验工作目录越近越可信，避免选到兄弟实验的同名 train.log。"""
    try:
        target = os.path.normcase(os.path.abspath(path))
    except Exception:
        return 0.0

    def under(base: str) -> bool:
        if not base:
            return False
        try:
            base = os.path.normcase(os.path.abspath(base))
        except Exception:
            return False
        if not base:
            return False
        return target == base or target.startswith(base + os.sep)

    if under(cwd):
        return 16.0
    if under(script_dir):
        return 12.0
    if script_dir:
        project = dirname(script_dir)
        if under(project):
            return 7.0
        if under(dirname(project)):
            return 3.0
    return 0.0


def _direct_score(path: str, cmdline: str, started_at: float,
                  parse_ok: bool) -> float:
    score = 10.0
    name = os.path.splitext(basename(path))[0]
    if os.path.isfile(path):
        score += 3.0
    if parse_ok:
        score += 8.0
    seed = _extract_seed(cmdline)
    if seed is not None and re.search(rf"(?<!\d){seed}(?!\d)", name):
        score += 22.0
    stem = _script_stem(cmdline).lower()
    if stem:
        tokens = [t for t in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", stem) if len(t) >= 2]
        if any(token in name.lower() for token in tokens):
            score += 7.0
    score += 6.0 * similarity(name, cmdline)
    if started_at:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        if mtime >= started_at - 60:
            score += 3.0
        if mtime >= time.time() - 300:
            score += 2.0
    if name == "queue":
        score -= 6.0
    if "\\logs\\" in path.lower() or "/logs/" in path.lower():
        score += 1.0
    return score


def discover_log(
    cwd: str,
    cmdline: str,
    ancestor_cmdlines: list[str],
    started_at: float,
    custom_rules: Optional[list] = None,
    allow_scan: bool = True,
    exclude: Optional[set[str]] = None,
    with_meta: bool = False,
):
    """寻找该实验正在写入的进度日志。

    返回日志路径；``with_meta=True`` 时返回 ``(路径, 是否来自命令行重定向)``。
    命令行里显式重定向的日志优先且会被信任（即使还没开始写）。
    """
    now = time.time()
    excluded = {str(item).lower() for item in (exclude or set())}
    cmdlines = [cmdline or ""] + list(ancestor_cmdlines or [])
    direct = [path for path in _redirect_candidates(cwd, cmdlines)
              if path.lower() not in excluded and not _is_app_data(path)]
    if direct:
        scored: list[tuple[float, str]] = []
        for path in direct:
            parsed_ok = False
            if os.path.isfile(path):
                try:
                    parsed_ok = parse_tail_text(read_tail(path, 16384), custom_rules) is not None
                except Exception:
                    parsed_ok = False
            scored.append((_direct_score(path, cmdline, started_at, parsed_ok), path))
        scored.sort(key=lambda item: -item[0])
        chosen = scored[0][1]
        log_line(f"discover_log: direct redirect -> {chosen} "
                 f"(score={scored[0][0]:.1f}, candidates={len(scored)})")
        return (chosen, True) if with_meta else chosen
    candidates: list[tuple[float, str]] = []
    if allow_scan:
        since = max(started_at - 300.0, now - 7200.0)
        seen_files: set[str] = set()
        prelim: list[tuple[float, str, float, int]] = []
        script_dir = _script_dir(cmdline)
        for root, depth in _scan_roots(cwd, cmdline):
            for path, mtime, size in _scan_dir(root, depth, since):
                key = path.lower()
                if key in seen_files or key in excluded or _is_app_data(path):
                    continue
                seen_files.add(key)
                prelim.append((_score_candidate(path, mtime, size, cmdline, started_at,
                                                False, cwd, script_dir),
                               path, mtime, size))
        prelim.sort(key=lambda item: -item[0])
        # 只对最有希望的若干文件做内容解析，避免扫描目录时读取过多文件
        for base_score, path, mtime, size in prelim[:16]:
            # 早于进程启动时间写入的日志不可能是这次实验的输出，直接排除
            if started_at and mtime < started_at - 60:
                continue
            parsed_ok = False
            try:
                tail = read_tail(path, max_bytes=32768)
                parsed_ok = parse_tail_text(tail, custom_rules) is not None
            except Exception:
                parsed_ok = False
            if not _is_relevant(path, cmdline, parsed_ok):
                continue
            score = base_score + (6.0 if parsed_ok else 0.0)
            candidates.append((score, path))
    if not candidates:
        return (None, False) if with_meta else None
    # 去重取最高分
    best: dict[str, float] = {}
    for score, path in candidates:
        key = path.lower()
        if score > best.get(key, -999):
            best[key] = score
    ranked = sorted(best.items(), key=lambda item: -item[1])
    if not ranked:
        return (None, False) if with_meta else None
    top_path, top_score = ranked[0]
    if top_score < 4.0:
        return (None, False) if with_meta else None
    log_line(f"discover_log: score={top_score:.1f} -> {top_path}")
    return (top_path, False) if with_meta else top_path


def read_tail(path: str, max_bytes: int = 65536) -> str:
    with open(path, "rb") as fh:
        try:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
        except OSError:
            fh.seek(0)
        data = fh.read(max_bytes)
    text = data.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text


# ---------------------------------------------------------------------------
# 单实验的进度跟踪器
# ---------------------------------------------------------------------------
class ProgressMiner:
    """负责一个实验会话的日志定位 + 进度解析 + 速率估计。"""

    def __init__(self, config, started_at: float, override: Optional[str] = None,
                 custom_rules: Optional[list] = None):
        self.config = config
        self.started_at = started_at
        self.state = ProgressState(override=override)
        self.custom_rules = compile_rules(custom_rules or [])
        self.allow_scan = bool(config.get("estimate.log_scan", True))
        self.stale_seconds = float(config.get("estimate.log_stale_seconds", 900.0))
        self._last_view: Optional[ProgressView] = None

    # -- 手动指定日志 ---------------------------------------------------
    def set_override(self, path: Optional[str]) -> None:
        self.state.override = path
        self.state.path = path
        self.state.direct = bool(path)
        self.state.last_size = -1
        self.state.last_mtime = 0.0
        self.state.samples.clear()
        self.state.last = None
        self.state.next_discover_ts = 0.0
        self.state.parse_fail_ticks = 0
        self._last_view = None

    # -- 主入口 ---------------------------------------------------------
    def update(self, now: float, cwd: str, cmdline: str,
               ancestor_cmdlines: list[str],
               exclude: Optional[set[str]] = None) -> Optional[ProgressView]:
        state = self.state
        if not state.override:
            state.override = self._config_override()
        path = state.path or state.override
        if not path and now >= state.next_discover_ts:
            found = discover_log(cwd, cmdline, ancestor_cmdlines, self.started_at,
                                 self.custom_rules, allow_scan=self.allow_scan,
                                 exclude=state.rejected | set(exclude or ()),
                                 with_meta=True)
            if found:
                path, is_direct = found
            else:
                path, is_direct = None, False
            state.discover_attempts += 1
            if path:
                state.path = path
                state.direct = bool(is_direct or state.override)
                state.next_discover_ts = now + 300.0
                log_line(f"progress log found: {path} direct={state.direct}")
            else:
                delay = 8.0 if state.discover_attempts < 8 else 60.0
                state.next_discover_ts = now + delay
        if not path:
            return None
        if not os.path.isfile(path):
            if state.direct:
                # 命令行指定的日志还没创建：继续等待，不切换
                return self._last_view
            state.path = None
            state.next_discover_ts = now + 10.0
            return self._last_view
        # 文件未变化时降低读取频率，但保证 10 秒一次以便更新速率
        try:
            stat = os.stat(path)
        except OSError:
            return self._last_view
        changed = stat.st_size != state.last_size or stat.st_mtime != state.last_mtime
        if not changed and now - state.last_read_ts < 10.0:
            return self._last_view
        state.last_read_ts = now
        state.last_size = stat.st_size
        state.last_mtime = stat.st_mtime
        try:
            text = read_tail(path)
        except Exception as exc:
            # 日志被实验进程独占/锁定或读失败：不能一直卡在这份日志上
            state.parse_fail_ticks += 1
            limit = 10 if state.direct else 3
            if state.parse_fail_ticks >= limit and state.path and not state.override:
                state.rejected.add(state.path.lower())
                log_line(f"progress log unreadable, rejected: {state.path} ({exc})")
                state.path = None
                state.direct = False
                state.parse_fail_ticks = 0
                state.next_discover_ts = now + 8.0
            return self._last_view
        parsed = parse_tail_text(text, self.custom_rules)
        if parsed:
            total_epochs = extract_total_epochs(cmdline, text)
            parsed = combine_epoch_progress(parsed, text, total_epochs)
        if not parsed:
            state.parse_fail_ticks += 1
            if state.parse_fail_ticks > 6 and state.path and not state.override:
                if state.direct:
                    # 命令行重定向的日志还没写出可识别内容，继续等待
                    state.parse_fail_ticks = 0
                else:
                    # 有文件但一直解析不出进度 → 排除它，换下一份候选日志
                    state.rejected.add(state.path.lower())
                    log_line(f"progress log rejected: {state.path}")
                    state.next_discover_ts = now + 15.0
                    state.path = None
                    state.direct = False
                    state.parse_fail_ticks = 0
            return self._last_view
        state.parse_fail_ticks = 0
        state.last_parse_ts = now
        self._ingest(now, parsed)
        view = self._build_view(now, parsed, path)
        state.last = parsed
        self._last_view = view
        return view

    def _config_override(self) -> Optional[str]:
        return None

    # -- 采样与速率 -----------------------------------------------------
    def _ingest(self, now: float, parsed: ParsedProgress) -> None:
        state = self.state
        if parsed.kind == "fraction" and parsed.cur is not None:
            value = parsed.cur
        elif parsed.pct is not None:
            value = parsed.pct
        else:
            return
        if state.samples:
            last_value = state.samples[-1].value
            if value < last_value - 1e-6:
                # 进入下一阶段 / 新任务，重置
                state.samples.clear()
            elif abs(value - last_value) < 1e-9 and now - state.samples[-1].ts < 20:
                return
        state.samples.append(ProgressSample(ts=now, value=value))
        # 保留最近 15 分钟的数据
        while state.samples and now - state.samples[0].ts > 900:
            state.samples.popleft()

    def _rate(self) -> Optional[float]:
        samples = self.state.samples
        if len(samples) < 2:
            return None
        first, last = samples[0], samples[-1]
        dt = last.ts - first.ts
        if dt < 5:
            return None
        delta = last.value - first.value
        if delta <= 0:
            return None
        return delta / dt

    def _build_view(self, now: float, parsed: ParsedProgress, path: str) -> ProgressView:
        state = self.state
        rate = self._rate()
        eta: Optional[float] = None
        if parsed.kind == "fraction" and parsed.total and parsed.cur is not None:
            pct = 100.0 * parsed.cur / parsed.total
            if rate and rate > 0:
                eta = max(0.0, (parsed.total - parsed.cur) / rate)
        else:
            pct = float(parsed.pct or 0.0)
            if rate and rate > 0:
                eta = max(0.0, (100.0 - pct) / rate)
        if parsed.tqdm_eta is not None and parsed.tqdm_eta >= 0:
            eta = parsed.tqdm_eta
        stale = (now - state.last_mtime) > max(180.0, self.stale_seconds / 2)
        if parsed.display_label:
            label = parsed.display_label
        elif parsed.kind == "fraction" and parsed.total:
            label = f"{_short_num(parsed.cur)}/{_short_num(parsed.total)}"
        else:
            label = f"{pct:.0f}%"
        source = f"日志 · {parsed.source}"
        return ProgressView(
            pct=max(0.0, min(100.0, pct)),
            label=label,
            rate=rate,
            eta_seconds=eta,
            source=source,
            stale=stale,
            current=parsed.cur,
            total=parsed.total,
            kind=parsed.kind,
            tqdm_eta=parsed.tqdm_eta,
        )


def compile_rules(rules: list) -> list[dict]:
    compiled: list[dict] = []
    for index, rule in enumerate(rules or []):
        if not isinstance(rule, dict):
            continue
        pattern = rule.get("pattern") or rule.get("regex")
        if not pattern:
            continue
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            log_line(f"bad custom progress rule {pattern!r}: {exc}")
            continue
        compiled.append({
            "name": rule.get("name") or f"自定义{index + 1}",
            "regex": regex,
        })
    return compiled


def _short_num(value: Optional[float]) -> str:
    if value is None:
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1000:.1f}k"
    return str(int(value))


def find_ancestor_cmdlines(procs: dict, pid: int, max_depth: int = 4) -> list[str]:
    """收集父进程的命令行（用于寻找重定向日志）。"""
    from .winproc import get_command_line

    cmdlines: list[str] = []
    current = procs.get(pid)
    seen: set[int] = set()
    depth = 0
    while current and current.ppid and depth < max_depth:
        if current.ppid in seen:
            break
        seen.add(current.ppid)
        parent = procs.get(current.ppid)
        if not parent:
            break
        try:
            line = get_command_line(parent.pid, parent.create_time)
        except Exception:
            line = None
        if line:
            cmdlines.append(line)
        current = parent
        depth += 1
    return cmdlines
