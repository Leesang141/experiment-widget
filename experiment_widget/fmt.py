"""数值 / 时间格式化工具。"""

from __future__ import annotations

import datetime as _dt
import math
import time
from typing import Optional


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def fmt_pct(value: Optional[float], digits: int = 0) -> str:
    """0~100 的百分比。小于 1% 但非 0 时显示一位小数。"""
    if value is None:
        return "—"
    if math.isnan(value) or math.isinf(value):
        return "—"
    value = max(0.0, value)
    if value < 1 and value > 0:
        return f"{value:.1f}%"
    if digits:
        return f"{value:.{digits}f}%"
    return f"{round(value)}%"


def fmt_cpu_pct(value: Optional[float]) -> str:
    """CPU 占整机百分比，保留一位小数（小数值时）。"""
    if value is None:
        return "—"
    if value >= 10:
        return f"{value:.0f}%"
    if value >= 1:
        return f"{value:.1f}%"
    if value > 0.05:
        return f"{value:.2f}%"
    return "0%"


def fmt_mb(mb: Optional[float]) -> str:
    """MB -> 人类可读。"""
    if mb is None:
        return "—"
    if mb >= 1024 * 10:
        return f"{mb / 1024:.1f} GB"
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    if mb >= 10:
        return f"{mb:.0f} MB"
    if mb > 0:
        return f"{mb:.1f} MB"
    return "0 MB"


def fmt_mb_short(mb: Optional[float]) -> str:
    if mb is None:
        return "—"
    if mb >= 1024:
        return f"{mb / 1024:.1f}G"
    if mb >= 1:
        return f"{mb:.0f}M"
    return "0"


def fmt_elapsed(seconds: float) -> str:
    """已运行时长：1:02:03 / 12:03 / 2天03:12。"""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}天 {hours:02d}:{minutes:02d}"
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fmt_elapsed_long(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分钟"
    return f"{seconds} 秒"


def fmt_eta(seconds: Optional[float]) -> str:
    """预计剩余时间，用于行内展示。"""
    if seconds is None:
        return "未知"
    seconds = max(0.0, seconds)
    if seconds < 10:
        return "即将完成"
    if seconds < 60:
        return f"≈ {int(seconds)} 秒"
    if seconds < 3600:
        return f"≈ {int(round(seconds / 60))} 分"
    if seconds < 86400:
        hours = int(seconds // 3600)
        minutes = int(round((seconds % 3600) / 60))
        if minutes >= 60:
            hours, minutes = hours + 1, 0
        if minutes == 0:
            return f"≈ {hours} 小时"
        return f"≈ {hours} 小时 {minutes} 分"
    days = seconds / 86400
    if days < 10:
        return f"≈ {days:.1f} 天"
    return f"≈ {int(round(days))} 天"


def fmt_eta_short(seconds: Optional[float]) -> str:
    if seconds is None:
        return "未知"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(round(seconds / 60))}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def fmt_clock(ts: Optional[float]) -> str:
    if not ts:
        return "—"
    return time.strftime("%H:%M:%S", time.localtime(ts))


def fmt_datetime(ts: Optional[float]) -> str:
    if not ts:
        return "—"
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts))


def fmt_age_minutes(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时"
    return f"{hours // 24} 天"


def today_str() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def parse_duration_text(text: str) -> Optional[float]:
    """解析用户输入的总时长，返回秒。支持 "90" / "90m" / "2h30m" / "1.5h" / "1:30"。"""
    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    try:
        if ":" in text:
            parts = [float(x) for x in text.split(":")]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            if len(parts) == 2:
                return parts[0] * 3600 + parts[1] * 60
            return None
    except ValueError:
        return None
    # 组合写法 2h30m / 1h / 90m / 45s / 1.5h
    import re

    total = 0.0
    matched = False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(天|d|小时|时|h|分钟|分|m|秒|s)", text):
        matched = True
        n = float(num)
        if unit in ("天", "d"):
            total += n * 86400
        elif unit in ("小时", "时", "h"):
            total += n * 3600
        elif unit in ("分钟", "分", "m"):
            total += n * 60
        else:
            total += n
    if matched:
        return total or None
    try:
        return float(text) * 60  # 纯数字按分钟处理
    except ValueError:
        return None
