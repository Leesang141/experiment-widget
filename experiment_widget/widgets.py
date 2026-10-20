"""通用自绘小组件：按钮、脉冲点、迷你折线图、进度条、气泡提示、弹出菜单。"""

from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Callable, Optional

from .theme import Theme


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------
def ellipsize(text: str, font, max_px: int, tail: str = "…") -> str:
    """按像素宽度截断文本。"""
    text = text or ""
    if max_px <= 0 or font.measure(text) <= max_px:
        return text
    tail_w = font.measure(tail)
    if tail_w >= max_px:
        return tail
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid]) + tail_w <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + tail


def ellipsize_left(text: str, font, max_px: int, lead: str = "…") -> str:
    """从左截断，保留路径尾部更有信息量的部分。"""
    text = text or ""
    if max_px <= 0 or font.measure(text) <= max_px:
        return text
    lead_w = font.measure(lead)
    if lead_w >= max_px:
        return lead
    for start in range(1, len(text)):
        if font.measure(text[start:]) + lead_w <= max_px:
            return lead + text[start:]
    return lead


def draw_rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                      radius: int, fill: str, outline: str = "", width: int = 1) -> int:
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(
        points, smooth=True, splinesteps=24, fill=fill,
        outline=outline or fill, width=width,
    )


# ---------------------------------------------------------------------------
# 按钮
# ---------------------------------------------------------------------------
class IconButton(tk.Label):
    def __init__(self, parent, theme: Theme, glyph: str, command: Callable[[], None],
                 tooltip: str = "", hover_bg: Optional[str] = None,
                 hover_fg: Optional[str] = None, size: int = 13,
                 bg: Optional[str] = None, fg: Optional[str] = None):
        self.theme = theme
        super().__init__(
            parent,
            text=glyph,
            font=theme.font(size, family=theme.icon_family or theme.ui_family),
            bg=bg or theme.c["panel"],
            fg=fg or theme.c["muted"],
            padx=theme.px(7),
            pady=theme.px(3),
            cursor="hand2",
        )
        self._command = command
        self._hover_bg = hover_bg or theme.c["card"]
        self._normal_bg = bg or theme.c["panel"]
        self._hover_fg = hover_fg or theme.c["text"]
        self._normal_fg = fg or theme.c["muted"]
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)
        self._tooltip = tooltip

    def _on_enter(self, _event=None):
        self.configure(bg=self._hover_bg, fg=self._hover_fg)

    def _on_leave(self, _event=None):
        self.configure(bg=self._normal_bg, fg=self._normal_fg)

    def _on_click(self, _event=None):
        self._on_leave()
        try:
            self._command()
        except Exception:
            pass


class TextButton(tk.Label):
    def __init__(self, parent, theme: Theme, text: str, command: Callable[[], None],
                 kind: str = "normal", tooltip: str = ""):
        palette = {
            "normal": (theme.c["card"], theme.c["text"], theme.c["border"]),
            "primary": (theme.c["cpu_dim"], theme.c["cpu"], theme.c["cpu_dim"]),
            "danger": (theme.c["red_dim"], theme.c["red"], theme.c["red_dim"]),
            "ghost": (theme.c["panel"], theme.c["muted"], theme.c["border"]),
        }
        bg, fg, border = palette.get(kind, palette["normal"])
        super().__init__(
            parent, text=text, font=theme.fonts["small"],
            bg=bg, fg=fg, padx=theme.px(12), pady=theme.px(5),
            highlightbackground=border, highlightthickness=1,
            cursor="hand2",
        )
        self.theme = theme
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = theme.mix(bg, theme.c["white"], 0.08)
        self._tooltip = tooltip
        self.bind("<Enter>", lambda e: self.configure(bg=self._hover_bg))
        self.bind("<Leave>", lambda e: self.configure(bg=self._bg))
        self.bind("<ButtonRelease-1>", self._on_click)

    def _on_click(self, _event=None):
        try:
            self._command()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 动画小点
# ---------------------------------------------------------------------------
class PulseDot(tk.Canvas):
    def __init__(self, parent, theme: Theme, size: int = 16, color: Optional[str] = None):
        super().__init__(parent, width=theme.px(size), height=theme.px(size),
                         bg=theme.c["panel"], highlightthickness=0)
        self.theme = theme
        self.size = size
        self.color = color or theme.c["green"]
        self.phase = 0
        self._job: Optional[str] = None
        self._draw()

    def set_color(self, color: str) -> None:
        self.color = color

    def _draw(self) -> None:
        self.delete("all")
        s = self.size
        cx = cy = self.theme.px(s) / 2
        base_r = self.theme.px(s * 0.22)
        ring_r = self.theme.px(s * (0.30 + 0.22 * self.phase))
        ring_color = self.theme.mix(self.color, self.theme.c["panel"], 0.55 - 0.35 * self.phase)
        self.create_oval(cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r,
                         fill="", outline=ring_color, width=self.theme.px(1))
        self.create_oval(cx - base_r, cy - base_r, cx + base_r, cy + base_r,
                         fill=self.color, outline="")
        self.phase = (self.phase + 1) % 3
        try:
            self._job = self.after(650, self._draw)
        except tk.TclError:
            pass

    def stop(self) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None


# ---------------------------------------------------------------------------
# 迷你折线图
# ---------------------------------------------------------------------------
class Sparkline(tk.Canvas):
    """CPU/GPU 最近一分钟走势的极简折线图。"""

    def __init__(self, parent, theme: Theme, height: int = 42):
        super().__init__(parent, bg=theme.c["panel"], highlightthickness=0,
                         height=theme.px(height), width=1)
        self.theme = theme
        self.h_px = theme.px(height)
        self.cpu: list[float] = []
        self.gpu: list[Optional[float]] = []
        self.bind("<Configure>", lambda e: self._redraw())

    def set_series(self, cpu: list[float], gpu: list[Optional[float]]) -> None:
        self.cpu = list(cpu)
        self.gpu = list(gpu)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return
        theme = self.theme
        pad_top = self.theme.px(4)
        usable = max(4, h - pad_top - self.theme.px(3))
        # 网格
        for frac in (0.0, 0.5, 1.0):
            y = pad_top + usable * frac
            self.create_line(0, y, w, y,
                             fill=theme.c["border_soft"] if frac else theme.c["border"],
                             dash=() if frac == 0.0 else (1, 3))
        inset = theme.px(7)
        if len(self.cpu) >= 2:
            self._draw_series(self.cpu, theme.c["cpu"], w, inset, pad_top, usable)
        gpu = self.gpu_filtered()
        if len(gpu) >= 2:
            self._draw_series(gpu, theme.c["gpu"], w, inset, pad_top, usable)

    def gpu_filtered(self) -> list[float]:
        return [float(v) for v in self.gpu if v is not None]

    def _draw_series(self, values: list[float], color: str, w: int, inset: int,
                     pad_top: int, usable: int) -> None:
        n = len(values)
        usable_w = max(2, w - 2 * inset)
        step = usable_w / max(1, n - 1)
        points: list[tuple[float, float]] = []
        for index, value in enumerate(values):
            x = inset + step * index
            y = pad_top + usable - max(0.0, min(100.0, value)) / 100.0 * usable
            points.append((x, y))
        if len(points) < 2:
            return
        flat = [coord for point in points for coord in point]
        self.create_line(flat, fill=color, width=self.theme.px(1.8),
                         smooth=True, splinesteps=18, capstyle="round",
                         joinstyle="round")
        # 末端亮点
        lx, ly = points[-1]
        r = self.theme.px(2.4)
        self.create_oval(lx - r, ly - r, lx + r, ly + r, fill=color, outline="")


# ---------------------------------------------------------------------------
# 细进度条
# ---------------------------------------------------------------------------
class ThinBar(tk.Canvas):
    def __init__(self, parent, theme: Theme, height: int = 4, track: Optional[str] = None):
        super().__init__(parent, bg=theme.c["card"], highlightthickness=0,
                         height=theme.px(height), width=1)
        self.theme = theme
        self.h_px = theme.px(height)
        self.track = track or theme.c["track"]
        self.pct = 0.0
        self.color = theme.c["green"]
        self.bind("<Configure>", lambda e: self._redraw())

    def set(self, pct: float, color: str, bg: Optional[str] = None) -> None:
        self.pct = max(0.0, min(100.0, pct))
        self.color = color
        if bg:
            self.configure(bg=bg)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 2 or h < 1:
            return
        self.create_rectangle(0, 0, w, h, fill=self.track, outline="")
        fill_w = max(2, int(w * self.pct / 100.0)) if self.pct > 0 else 0
        if fill_w:
            self.create_rectangle(0, 0, fill_w, h, fill=self.color, outline="")


# ---------------------------------------------------------------------------
# 侧边滚动条
# ---------------------------------------------------------------------------
class ScrollBar(tk.Canvas):
    """细长的侧边滚动条：悬停加亮、拖动滑块、点击轨道翻页。"""

    def __init__(self, parent, theme: Theme, width: int = 12,
                 wheel_handler=None):
        self.theme = theme
        self.width_px = theme.px(width)
        super().__init__(parent, width=self.width_px, bg=theme.c["panel"],
                         highlightthickness=0, bd=0, cursor="arrow")
        self._target: Optional[tk.Canvas] = None
        self._wheel_handler = wheel_handler
        self._hover = False
        self._drag = None
        self._thumb = (0.0, 0.0)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Configure>", lambda e: self.update_view())

    def attach(self, target: tk.Canvas) -> None:
        self._target = target
        self.update_view()

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self.update_view()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._drag = None
        self.update_view()

    def _on_wheel(self, event) -> None:
        if self._wheel_handler is not None:
            self._wheel_handler(event)
            self.update_view()

    def update_view(self) -> None:
        self.delete("all")
        target = self._target
        if target is None:
            return
        theme = self.theme
        try:
            view_h = target.winfo_height()
            bbox = target.bbox("all")
            content_h = bbox[3] if bbox else 0
            first, last = target.yview()
        except Exception:
            return
        if view_h < 20:
            return
        if content_h <= view_h + 2 or (last - first) >= 0.999:
            return
        width = self.winfo_width() or self.width_px
        cx = width / 2
        track_w = theme.px(3 if not self._hover else 4)
        y1 = theme.px(4)
        y2 = view_h - theme.px(4)
        track_color = theme.mix(theme.c["panel"], theme.c["border"], 0.45)
        if self._hover:
            track_color = theme.mix(theme.c["panel"], theme.c["border"], 0.75)
        draw_rounded_rect(self, cx - track_w / 2, y1, cx + track_w / 2, y2,
                          track_w / 2, fill=track_color)
        thumb_h = max(theme.px(26), (last - first) * (y2 - y1))
        thumb_top = y1 + first * ((y2 - y1) - thumb_h)
        thumb_w = theme.px(6 if not self._hover else 8)
        thumb_color = theme.mix(theme.c["border"], theme.c["muted"], 0.62)
        if self._hover:
            thumb_color = theme.mix(theme.c["muted"], theme.c["text"], 0.35)
        draw_rounded_rect(self, cx - thumb_w / 2, thumb_top,
                          cx + thumb_w / 2, thumb_top + thumb_h,
                          thumb_w / 2, fill=thumb_color)
        self._thumb = (thumb_top, thumb_top + thumb_h)

    def _on_press(self, event) -> None:
        target = self._target
        if target is None:
            return
        view_h = target.winfo_height()
        top, bottom = self._thumb
        if top <= event.y <= bottom:
            self._drag = (event.y, target.canvasy(0))
        else:
            direction = -1 if event.y < top else 1
            target.yview_scroll(int(direction * max(1, view_h / self.theme.px(20))), "units")
            self._drag = None
            self.update_view()

    def _on_drag(self, event) -> None:
        target = self._target
        if target is None or not self._drag:
            return
        start_y, start_offset = self._drag
        view_h = max(1, target.winfo_height())
        bbox = target.bbox("all")
        content_h = bbox[3] if bbox else 0
        if content_h <= view_h:
            return
        offset = start_offset + (event.y - start_y) * content_h / view_h
        offset = max(0.0, min(content_h - view_h, offset))
        target.yview_moveto(offset / content_h)
        self.update_view()

    def _on_release(self, _event=None) -> None:
        self._drag = None


# ---------------------------------------------------------------------------
# 气泡提示
# ---------------------------------------------------------------------------
class Tooltip:
    def __init__(self, root: tk.Tk, theme: Theme):
        self.root = root
        self.theme = theme
        self._win: Optional[tk.Toplevel] = None
        self._job: Optional[str] = None
        self._widget: Optional[tk.Widget] = None
        self._provider: Optional[Callable[[], str]] = None

    def attach(self, widget: tk.Widget, provider: Callable[[], str], delay: int = 500) -> None:
        widget.bind("<Enter>", lambda e, w=widget, p=provider: self._schedule(w, p, delay), add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")
        widget.bind("<ButtonPress>", lambda e: self.hide(), add="+")

    def _schedule(self, widget: tk.Widget, provider: Callable[[], str], delay: int) -> None:
        self._cancel()
        self._widget = widget
        self._provider = provider
        self._job = self.root.after(delay, self._show)

    def _cancel(self) -> None:
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        if not self._widget or not self._provider:
            return
        try:
            text = self._provider()
        except Exception:
            text = ""
        if not text:
            return
        self.hide()
        try:
            x = self.root.winfo_pointerx()
            y = self.root.winfo_pointery()
        except Exception:
            x, y = 0, 0
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        theme = self.theme
        win.configure(bg=theme.c["border"])
        frame = tk.Frame(win, bg=theme.c["panel_alt"], padx=theme.px(10), pady=theme.px(8))
        frame.pack(fill="both", expand=True, padx=theme.px(1), pady=theme.px(1))
        label = tk.Label(
            frame, text=text, font=theme.fonts["small"], fg=theme.c["text2"],
            bg=theme.c["panel_alt"], justify="left", anchor="w",
            wraplength=theme.px(380),
        )
        label.pack()
        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        pos_x = min(max(8, x + theme.px(14)), screen_w - width - 8)
        pos_y = min(max(8, y + theme.px(18)), screen_h - height - 8)
        win.geometry(f"+{pos_x}+{pos_y}")
        self._win = win
        self._job = None

    def hide(self) -> None:
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None

    def maybe_move(self) -> None:
        pass


def _relative_y(widget: tk.Widget, ancestor: tk.Widget) -> int:
    """计算 widget 相对 ancestor 的 y 坐标（不依赖窗口是否已映射到屏幕）。"""
    y = 0
    node: Optional[tk.Widget] = widget
    while node is not None and node is not ancestor:
        try:
            y += node.winfo_y()
            node = node.master
        except Exception:
            break
    return y


# ---------------------------------------------------------------------------
# 右键菜单
# ---------------------------------------------------------------------------
class PopMenu:
    """深色自绘右键菜单。items: ("label", callback) / ("sep", None) / ("header", text)。"""

    def __init__(self, root: tk.Tk, theme: Theme):
        self.root = root
        self.theme = theme
        self._win: Optional[tk.Toplevel] = None
        self._root_bind: Optional[str] = None
        self._items: list[tuple[int, int, object]] = []

    def show(self, x: int, y: int, items: list[tuple]) -> None:
        self.close()
        theme = self.theme
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=theme.c["border"])
        inner = tk.Frame(win, bg=theme.c["panel_alt"], padx=theme.px(4), pady=theme.px(4))
        inner.pack(fill="both", expand=True, padx=theme.px(1), pady=theme.px(1))
        labels: list[tk.Label] = []
        clickable: list[tuple[tk.Label, object]] = []
        for item in items:
            kind = item[0] if item else "sep"
            if kind == "sep":
                sep = tk.Frame(inner, bg=theme.c["border_soft"], height=1)
                sep.pack(fill="x", padx=theme.px(8), pady=theme.px(4))
                continue
            if kind == "header":
                label = tk.Label(inner, text=str(item[1]), font=theme.fonts["tiny"],
                                 fg=theme.c["muted"], bg=theme.c["panel_alt"],
                                 anchor="w", padx=theme.px(10), pady=theme.px(3))
                label.pack(fill="x")
                continue
            text = str(item[1])
            callback = item[2] if len(item) > 2 else None
            danger = len(item) > 3 and item[3] == "danger"
            fg = theme.c["red"] if danger else theme.c["text2"]
            label = tk.Label(inner, text=text, font=theme.fonts["small"], fg=fg,
                             bg=theme.c["panel_alt"], anchor="w",
                             padx=theme.px(10), pady=theme.px(6), cursor="hand2")
            label.pack(fill="x")
            label._normal_bg = theme.c["panel_alt"]  # type: ignore[attr-defined]
            label._hover_bg = theme.mix(theme.c["panel_alt"], theme.c["white"], 0.08)  # type: ignore[attr-defined]
            label.bind("<Enter>", lambda e, lb=label: lb.configure(bg=lb._hover_bg))  # type: ignore[attr-defined]
            label.bind("<Leave>", lambda e, lb=label: lb.configure(bg=lb._normal_bg))  # type: ignore[attr-defined]
            if callback:
                clickable.append((label, callback))
            labels.append(label)
        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        pos_x = min(max(8, x), screen_w - width - 8)
        pos_y = min(max(8, y), screen_h - height - 8)
        win.geometry(f"+{pos_x}+{pos_y}")
        try:
            win.update()  # 强制映射并完成布局，之后子控件的 winfo_y 才准确
        except Exception:
            win.update_idletasks()
        # 用坐标命中菜单项：Toplevel 自身能收到所有子控件的点击事件，
        # 不依赖子 Label 的按钮绑定（某些环境/抓取下子控件收不到事件）。
        self._items = [
            (_relative_y(label, win),
             _relative_y(label, win) + label.winfo_height(),
             callback)
            for label, callback in clickable
        ]
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<ButtonPress-1>", self._on_menu_click)
        win.bind("<FocusOut>", lambda e: self.close())
        # 不使用 grab_set：指针抓取会导致菜单项收不到点击，表现为“点菜单没反应”。
        self._root_bind = self.root.bind("<Button-1>", self._on_root_click, add="+")
        win.focus_force()
        self._win = win

    def _on_root_click(self, _event=None) -> None:
        self.close()

    def _on_menu_click(self, event) -> None:
        win = self._win
        if win is None:
            return
        widget = getattr(event, "widget", win)
        try:
            y = _relative_y(widget, win) + event.y
        except Exception:
            y = getattr(event, "y", 0)
        for top, bottom, callback in self._items:
            if top <= y < bottom:
                self._invoke(callback)
                return
        self.close()

    def _invoke(self, callback) -> None:
        self.close()
        try:
            callback()
        except Exception:
            pass

    def close(self) -> None:
        if self._root_bind is not None:
            try:
                self.root.unbind("<Button-1>", self._root_bind)
            except Exception:
                pass
            self._root_bind = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None

    @property
    def visible(self) -> bool:
        return self._win is not None


# ---------------------------------------------------------------------------
# 悬停工具
# ---------------------------------------------------------------------------
def bind_tree_hover(root: tk.Widget, bg_normal: str, bg_hover: str,
                    on_click=None, on_context=None) -> None:
    """给一个容器和它的全部子控件绑定悬停/点击事件。"""
    def apply(widget: tk.Widget) -> None:
        try:
            if str(widget.cget("bg")) == bg_normal:
                widget.bind("<Enter>", lambda e: _recolor(root, bg_normal, bg_hover), add="+")
                widget.bind("<Leave>", lambda e: _recolor(root, bg_normal, bg_hover, hover=False), add="+")
        except Exception:
            pass
        if on_click:
            widget.bind("<ButtonRelease-1>", on_click, add="+")
        if on_context:
            widget.bind("<Button-3>", on_context, add="+")
        for child in widget.winfo_children():
            apply(child)

    apply(root)


def _recolor(root: tk.Widget, normal: str, hover: str, hover_enter: bool = True) -> None:
    target = hover if hover_enter else normal

    def walk(widget: tk.Widget) -> None:
        try:
            current = str(widget.cget("bg"))
        except Exception:
            return
        if current in (normal, hover) or current == normal:
            try:
                widget.configure(bg=target)
            except Exception:
                pass
        for child in widget.winfo_children():
            walk(child)

    walk(root)
