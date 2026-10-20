"""界面主题：颜色、字体、DPI 缩放。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

# 深色 HUD 配色
COLORS = {
    "key": "#0a0f15",          # 透明色键（窗口圆角外）
    "panel": "#0f1620",        # 面板底色
    "panel_alt": "#111a25",
    "card": "#141d28",
    "card_hover": "#1a2532",
    "border": "#243244",
    "border_soft": "#1b2735",
    "text": "#e9eff8",
    "text2": "#aebccd",
    "muted": "#7d8b9e",
    "faint": "#546274",
    "track": "#1d2836",
    "accent": "#4cc9f0",
    "cpu": "#38bdf8",
    "cpu_dim": "#0f2f42",
    "gpu": "#a78bfa",
    "gpu_dim": "#241f3f",
    "green": "#34d399",
    "green_dim": "#123328",
    "amber": "#fbbf24",
    "amber_dim": "#33290f",
    "red": "#fb7185",
    "red_dim": "#3a1721",
    "white": "#ffffff",
}

FOLDER_ACCENTS = ["#38bdf8", "#a78bfa", "#34d399", "#22d3ee", "#818cf8", "#5eead4"]


def folder_accent(folder: str) -> str:
    if not folder:
        return COLORS["accent"]
    value = 0
    for ch in folder:
        value = (value * 131 + ord(ch)) & 0xFFFFFFFF
    return FOLDER_ACCENTS[value % len(FOLDER_ACCENTS)]


def _pick_family(root: tk.Misc, candidates: list[str], fallback: str) -> str:
    try:
        available = {name.lower(): name for name in tkfont.families(root)}
    except Exception:
        return fallback
    for candidate in candidates:
        if candidate.lower() in available:
            return available[candidate.lower()]
    return fallback


class Theme:
    """把设计稿上的 96-DPI 像素换算成当前屏幕像素。"""

    def __init__(self, root: tk.Misc):
        self.root = root
        try:
            dpi = root.winfo_fpixels("1i")
        except Exception:
            dpi = 96.0
        self.scale = max(1.0, float(dpi) / 96.0)
        self.ui_family = _pick_family(
            root, ["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Segoe UI"], "Segoe UI")
        self.mono_family = _pick_family(
            root, ["Cascadia Mono", "JetBrains Mono", "Consolas", "Courier New"], "Consolas")
        self.icon_family = _pick_family(
            root, ["Segoe Fluent Icons", "Segoe MDL2 Assets"], "")
        self.c = COLORS
        self.fonts = {
            "title": self.font(15, "bold"),
            "name": self.font(13, "bold"),
            "section": self.font(12.5, "bold"),
            "body": self.font(12),
            "body_bold": self.font(12, "bold"),
            "small": self.font(11),
            "small_bold": self.font(11, "bold"),
            "tiny": self.font(10),
            "mono": self.font(11, family=self.mono_family),
            "mono_small": self.font(10.5, family=self.mono_family),
            "big": self.font(18, "bold", family=self.mono_family),
            "icon": self.font(13, family=self.icon_family or self.ui_family),
            "icon_small": self.font(11, family=self.icon_family or self.ui_family),
        }

    # -- 尺寸 / 字体 ----------------------------------------------------
    def px(self, value: float) -> int:
        return max(1, int(round(value * self.scale)))

    def font(self, size_px: float, weight: str = "normal", family: str | None = None):
        return tkfont.Font(
            root=self.root,
            family=family or self.ui_family,
            size=-self.px(size_px),
            weight=weight,
        )

    # -- 颜色混合 -------------------------------------------------------
    @staticmethod
    def mix(color_a: str, color_b: str, ratio: float) -> str:
        ratio = max(0.0, min(1.0, ratio))

        def parse(color: str) -> tuple[int, int, int]:
            color = color.lstrip("#")
            return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)

        ar, ag, ab = parse(color_a)
        br, bg, bb = parse(color_b)
        return "#%02x%02x%02x" % (
            int(ar + (br - ar) * ratio),
            int(ag + (bg - ag) * ratio),
            int(ab + (bb - ab) * ratio),
        )
