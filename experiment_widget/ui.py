"""主窗口：无边框深色小面板、拖拽、折叠、滚动卡片与设置入口。"""

from __future__ import annotations

import time
import tkinter as tk
from collections import deque
from typing import Callable, Optional

from . import fmt, winproc
from .cards import FolderCard
from .config import Config
from .models import ExperimentView, FolderView, Snapshot
from .theme import Theme
from .widgets import (
    IconButton,
    PopMenu,
    PulseDot,
    ScrollBar,
    Sparkline,
    Tooltip,
    draw_rounded_rect,
    ellipsize,
)

DESIGN_WIDTH = 420
DESIGN_HEIGHT_MIN = 150
DESIGN_CARDS_MAX = 440


class MainWindow:
    def __init__(self, app, config: Config):
        self.app = app
        self.config = config
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("实验监控")
        self.root.overrideredirect(True)
        self.theme = Theme(self.root)
        self.c = self.theme.c
        self._collapsed = bool(config.get("window.collapsed", False))
        self._loaded = False
        self._snapshot: Optional[Snapshot] = None
        self._cards: dict[str, FolderCard] = {}
        self._card_order: list[str] = []
        self._height_job: Optional[str] = None
        self._last_height = 0
        self._scroll_offset = 0
        self._cpu_series: deque = deque(maxlen=30)
        self._gpu_series: deque = deque(maxlen=30)
        self._drag_anchor = None
        self._moved = False
        self._hidden_count = 0
        self._toast_job: Optional[str] = None

        self.tooltip = Tooltip(self.root, self.theme)
        self.menu = PopMenu(self.root, self.theme)

        self._apply_window_attrs()
        self._build()
        self._place_window()
        self.root.deiconify()
        self._bind_global()

    # ==================================================================
    # 构建
    # ==================================================================
    def _apply_window_attrs(self) -> None:
        self._topmost = bool(self.config.get("window.always_on_top", True))
        self.root.attributes("-topmost", self._topmost)
        try:
            self.root.attributes("-alpha", float(self.config.get("window.opacity", 0.98)))
        except Exception:
            pass
        self.root.configure(bg=self.c["key"])
        try:
            self.root.wm_attributes("-transparentcolor", self.c["key"])
        except Exception:
            pass

    def _build(self) -> None:
        theme = self.theme
        self.canvas = tk.Canvas(self.root, bg=self.c["key"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self._bg_id = draw_rounded_rect(
            self.canvas, 0, 0, theme.px(DESIGN_WIDTH), theme.px(200),
            theme.px(16), fill=self.c["panel"], outline=self.c["border"],
            width=theme.px(1))
        self.content = tk.Frame(self.canvas, bg=self.c["panel"])
        self._content_id = self.canvas.create_window(
            theme.px(14), theme.px(14), anchor="nw", window=self.content)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._bind_window_drag(self.canvas)
        self._build_header()
        self._build_body()

    # -- 头部 -----------------------------------------------------------
    def _build_header(self) -> None:
        theme = self.theme
        header = tk.Frame(self.content, bg=self.c["panel"], height=theme.px(34))
        header.pack(fill="x")
        header.pack_propagate(False)
        self.header = header
        self.pulse = PulseDot(header, theme, size=16)
        self.pulse.pack(side="left", padx=(theme.px(2), theme.px(6)), pady=(theme.px(5), 0))
        self.title = tk.Label(header, text="实验监控", font=theme.fonts["title"],
                              bg=self.c["panel"], fg=self.c["text"])
        self.title.pack(side="left", pady=(theme.px(2), 0))
        self.header_summary = tk.Label(header, text="", font=theme.fonts["small"],
                                       bg=self.c["panel"], fg=self.c["muted"])
        buttons = tk.Frame(header, bg=self.c["panel"])
        buttons.pack(side="right")
        self.btn_close = IconButton(buttons, theme, "\uE8BB", self.app.quit_app,
                                    tooltip="退出", hover_bg=self.c["red_dim"],
                                    hover_fg=self.c["red"])
        self.btn_close.pack(side="right", padx=(0, theme.px(1)))
        self.btn_collapse = IconButton(buttons, theme, "\uE70D",
                                       self._toggle_collapsed, tooltip="收起/展开")
        self.btn_collapse.pack(side="right", padx=(0, theme.px(1)))
        self.btn_settings = IconButton(buttons, theme, "\uE713",
                                       self.app.open_settings, tooltip="设置")
        self.btn_settings.pack(side="right", padx=(0, theme.px(1)))
        self.btn_pin = IconButton(buttons, theme, self._pin_glyph(),
                                  self._toggle_pin, tooltip="置顶显示")
        self.btn_pin.pack(side="right", padx=(0, theme.px(1)))
        self._pin_style()

        for widget in (header, self.pulse, self.title, self.header_summary):
            self._bind_window_drag(widget)
        for button in (self.btn_close, self.btn_collapse, self.btn_settings, self.btn_pin):
            self._attach_button_tooltip(button)

    def _attach_button_tooltip(self, button: IconButton) -> None:
        text = getattr(button, "_tooltip", "")
        if not text:
            return
        self.tooltip.attach(button, lambda t=text: t, delay=650)

    # -- 主体 -----------------------------------------------------------
    def _build_body(self) -> None:
        theme = self.theme
        self.body = tk.Frame(self.content, bg=self.c["panel"])
        self.body.pack(fill="both", expand=True)

        stats = tk.Frame(self.body, bg=self.c["panel"])
        stats.pack(fill="x", pady=(theme.px(2), 0))
        self.stats = stats
        row = tk.Frame(stats, bg=self.c["panel"])
        row.pack(fill="x")
        self.summary_label = tk.Label(row, text="", font=theme.fonts["tiny"],
                                      bg=self.c["panel"], fg=self.c["muted"])
        self.summary_label.pack(side="right", pady=(theme.px(2), 0))
        self.cpu_key = tk.Label(row, text="CPU", font=theme.fonts["small"],
                                bg=self.c["panel"], fg=self.c["muted"])
        self.cpu_key.pack(side="left")
        self.cpu_value = tk.Label(row, text="—", font=theme.fonts["big"],
                                  bg=self.c["panel"], fg=self.c["cpu"])
        self.cpu_value.pack(side="left", padx=(theme.px(5), 0))
        self._stat_sep(row)
        self.gpu_key = tk.Label(row, text="GPU", font=theme.fonts["small"],
                                bg=self.c["panel"], fg=self.c["muted"])
        self.gpu_key.pack(side="left")
        self.gpu_value = tk.Label(row, text="—", font=theme.fonts["big"],
                                  bg=self.c["panel"], fg=self.c["gpu"])
        self.gpu_value.pack(side="left", padx=(theme.px(5), 0))
        self.spark = Sparkline(stats, theme, height=38)
        self.spark.pack(fill="x", pady=(theme.px(2), 0))
        for widget in (stats, row, self.cpu_key, self.cpu_value, self.gpu_key,
                       self.gpu_value, self.summary_label, self.spark):
            self._bind_window_drag(widget)

        scroll_host = tk.Frame(self.body, bg=self.c["panel"])
        scroll_host.pack(fill="both", expand=True, pady=(theme.px(4), 0))
        self.scroll_host = scroll_host
        self.cards_canvas = tk.Canvas(scroll_host, bg=self.c["panel"],
                                      highlightthickness=0, bd=0, height=1,
                                      yscrollincrement=theme.px(20))
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar = ScrollBar(scroll_host, theme, width=12,
                                   wheel_handler=self._on_mousewheel)
        self.scrollbar.pack(side="right", fill="y", padx=(theme.px(3), 0))
        self.cards_inner = tk.Frame(self.cards_canvas, bg=self.c["panel"])
        self._cards_window = self.cards_canvas.create_window(
            0, 0, anchor="nw", window=self.cards_inner)
        self.cards_canvas.bind("<Configure>", self._on_cards_configure)
        self.cards_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.cards_inner.bind("<MouseWheel>", self._on_mousewheel)
        self.scrollbar.attach(self.cards_canvas)
        self.tooltip.attach(self.scrollbar,
                            lambda: "滚动列表（拖动滑块 / 滚轮）", delay=800)

        self.empty = self._build_empty_state(self.cards_inner)
        self.empty.pack(fill="x", pady=(theme.px(8), theme.px(4)))

        footer = tk.Frame(self.body, bg=self.c["panel"])
        footer.pack(fill="x", pady=(theme.px(5), 0))
        self.footer = footer
        self.footer_left = tk.Label(footer, text="正在扫描实验…",
                                    font=theme.fonts["tiny"],
                                    bg=self.c["panel"], fg=self.c["muted"], anchor="w")
        self.footer_left.pack(side="left")
        self.footer_right = tk.Label(footer, text="", font=theme.fonts["tiny"],
                                     bg=self.c["panel"], fg=self.c["faint"], anchor="e")
        self.footer_right.pack(side="right")
        for widget in (footer, self.footer_left, self.footer_right):
            self._bind_window_drag(widget)
        self._apply_collapsed(self._collapsed, initial=True)

    def _stat_sep(self, parent) -> None:
        tk.Label(parent, text="·", font=self.theme.fonts["body"],
                 bg=self.c["panel"], fg=self.c["faint"]).pack(
            side="left", padx=self.theme.px(8), pady=(self.theme.px(2), 0))

    def _build_empty_state(self, parent) -> tk.Frame:
        theme = self.theme
        frame = tk.Frame(parent, bg=self.c["panel"])
        icon = tk.Label(frame, text="◎", font=theme.font(30, family=theme.ui_family),
                        bg=self.c["panel"], fg=self.c["faint"])
        icon.pack(pady=(theme.px(18), 0))
        self.empty_title = tk.Label(frame, text="正在扫描后台实验…",
                                    font=theme.fonts["body_bold"],
                                    bg=self.c["panel"], fg=self.c["text2"])
        self.empty_title.pack(pady=(theme.px(4), 0))
        self.empty_hint = tk.Label(
            frame,
            text="有 CPU / GPU 占用的实验会自动显示在这里\n悬停可看命令，右键可设置预计时长",
            font=theme.fonts["tiny"], bg=self.c["panel"], fg=self.c["faint"],
            justify="center", wraplength=theme.px(320))
        self.empty_hint.pack(pady=(theme.px(4), theme.px(16)))
        return frame

    # ==================================================================
    # 位置与尺寸
    # ==================================================================
    def _place_window(self) -> None:
        theme = self.theme
        width = theme.px(DESIGN_WIDTH)
        height = theme.px(160)
        vx, vy, vw, vh = winproc.virtual_screen_rect()
        margin = theme.px(24)
        x = self.config.get("window.x")
        y = self.config.get("window.y")
        if x is None or y is None:
            x = vx + vw - width - margin
            y = vy + margin
        else:
            x = int(x)
            y = int(y)
        x = max(vx, min(x, vx + vw - width))
        y = max(vy, min(y, vy + vh - height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _on_canvas_configure(self, event) -> None:
        theme = self.theme
        pad = theme.px(14)
        width = max(2, event.width)
        height = max(2, event.height)
        self.canvas.coords(self._bg_id,
                           *self._rounded_points(0, 0, width, height,
                                                 theme.px(16)))
        self.canvas.itemconfigure(self._content_id, width=max(2, width - 2 * pad),
                                  height=max(2, height - 2 * pad))

    def _rounded_points(self, x1, y1, x2, y2, radius) -> list[float]:
        # 用与 draw_rounded_rect 相同的点集更新已有 item
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return points

    def _schedule_height(self) -> None:
        if self._height_job:
            return
        self._height_job = self.root.after(20, self._update_height)

    def _update_height(self) -> None:
        self._height_job = None
        theme = self.theme
        pad = theme.px(14)
        try:
            header_h = self.header.winfo_reqheight()
        except Exception:
            header_h = theme.px(34)
        if self._collapsed:
            total = max(theme.px(46), pad * 2 + header_h)
        else:
            try:
                self.body.update_idletasks()
                stats_h = self.stats.winfo_reqheight()
                footer_h = self.footer.winfo_reqheight()
                content_h = self.cards_inner.winfo_reqheight()
            except Exception:
                stats_h, footer_h, content_h = theme.px(70), theme.px(18), theme.px(120)
            cards_h = max(theme.px(64), min(content_h, theme.px(DESIGN_CARDS_MAX)))
            total = pad * 2 + header_h + stats_h + footer_h + theme.px(9) + cards_h
            total = max(theme.px(DESIGN_HEIGHT_MIN), total)
        if abs(total - self._last_height) <= 1:
            return
        self._last_height = total
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            width = theme.px(DESIGN_WIDTH)
            self.root.geometry(f"{width}x{total}+{x}+{y}")
        except Exception:
            pass

    # ==================================================================
    # 折叠
    # ==================================================================
    def _pin_glyph(self) -> str:
        if not self.theme.icon_family:
            return "●"
        return "\uE718" if self._topmost else "\uE77A"

    def _pin_style(self) -> None:
        glyph = self._pin_glyph()
        self.btn_pin.configure(text=glyph)
        self.btn_pin._normal_fg = self.c["cpu"] if self._topmost else self.c["muted"]
        self.btn_pin.configure(fg=self.btn_pin._normal_fg)

    def _toggle_pin(self) -> None:
        self._topmost = not self._topmost
        self.root.attributes("-topmost", self._topmost)
        self._pin_style()
        self.app.apply_setting("window.always_on_top", self._topmost)

    def set_topmost(self, value: bool) -> None:
        self._topmost = bool(value)
        self.root.attributes("-topmost", self._topmost)
        self._pin_style()

    def set_opacity(self, value: float) -> None:
        try:
            self.root.attributes("-alpha", float(value))
        except Exception:
            pass

    def _toggle_collapsed(self) -> None:
        self._apply_collapsed(not self._collapsed)
        self.app.apply_setting("window.collapsed", self._collapsed)
        self._schedule_height()

    def _apply_collapsed(self, collapsed: bool, initial: bool = False) -> None:
        self._collapsed = bool(collapsed)
        theme = self.theme
        if self._collapsed:
            if not initial:
                self.body.pack_forget()
            else:
                self.body.pack_forget()
            self.header_summary.pack(side="left", padx=(theme.px(10), 0),
                                     pady=(theme.px(3), 0))
            self.btn_settings.pack_forget()
            self.btn_collapse.configure(text="\uE70E")
            self._update_collapsed_summary()
        else:
            self.header_summary.pack_forget()
            self.btn_collapse.configure(text="\uE70D")
            # 重新排列按钮，保证徽标顺序稳定
            self.btn_pin.pack_forget()
            self.btn_settings.pack_forget()
            self.btn_collapse.pack_forget()
            self.btn_close.pack_forget()
            self.btn_close.pack(side="right", padx=(0, theme.px(1)))
            self.btn_collapse.pack(side="right", padx=(0, theme.px(1)))
            self.btn_settings.pack(side="right", padx=(0, theme.px(1)))
            self.btn_pin.pack(side="right", padx=(0, theme.px(1)))
            self.body.pack(fill="both", expand=True)
        if not initial:
            self._schedule_height()

    def _update_collapsed_summary(self) -> None:
        if not self._collapsed or self._snapshot is None:
            return
        snap = self._snapshot
        parts = [f"CPU {fmt.fmt_pct(snap.system.cpu_pct)}"]
        if snap.system.gpu_util is not None:
            parts.append(f"GPU {fmt.fmt_pct(snap.system.gpu_util)}")
        parts.append(f"{snap.total_experiments} 实验")
        self.header_summary.configure(text=" · ".join(parts))

    # ==================================================================
    # 数据更新
    # ==================================================================
    def update_snapshot(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._loaded = True
        self._append_history(snapshot)
        self._update_stats(snapshot)
        self._update_cards(snapshot)
        self._update_footer(snapshot)
        self._update_collapsed_summary()
        self._schedule_height()

    def _append_history(self, snapshot: Snapshot) -> None:
        self._cpu_series.append(snapshot.system.cpu_pct)
        self._gpu_series.append(snapshot.system.gpu_util)

    def _update_stats(self, snapshot: Snapshot) -> None:
        system = snapshot.system
        self.cpu_value.configure(text=fmt.fmt_pct(system.cpu_pct))
        self.gpu_value.configure(
            text=fmt.fmt_pct(system.gpu_util) if system.gpu_util is not None else "—")
        folders = len([f for f in snapshot.folders
                       if any(not e.hidden for e in f.experiments)])
        parts = [f"{snapshot.total_experiments} 实验"]
        if folders:
            parts.append(f"{folders} 文件夹")
        if system.vram_used_mb is not None and system.vram_total_mb:
            vram = f"{system.vram_used_mb / 1024:.1f}/{system.vram_total_mb / 1024:.1f}G"
            parts.append(f"显存 {vram}")
        self.summary_label.configure(text=" · ".join(parts))
        self.spark.set_series(list(self._cpu_series), list(self._gpu_series))
        if snapshot.total_experiments == 0:
            self.pulse.set_color(self.c["faint"])
        else:
            self.pulse.set_color(self.c["green"])

    def _update_cards(self, snapshot: Snapshot) -> None:
        visible: list[FolderView] = []
        hidden = 0
        for folder in snapshot.folders:
            experiments = [exp for exp in folder.experiments if not exp.hidden]
            hidden += len(folder.experiments) - len(experiments)
            if not experiments:
                continue
            visible.append(folder)
        keys = [folder.folder or "未知" for folder in visible]
        for key in list(self._cards.keys()):
            if key not in keys:
                self._cards[key].destroy()
                self._cards.pop(key, None)
        for folder in visible:
            key = folder.folder or "未知"
            if key in self._cards:
                self._cards[key].update(folder)
            else:
                self._cards[key] = FolderCard(
                    self.cards_inner, self.theme, folder, self._card_callbacks())
        if keys != self._card_order:
            self._card_order = keys
            for card in self._cards.values():
                card.frame.pack_forget()
            for index, key in enumerate(keys):
                card = self._cards.get(key)
                if card:
                    card.frame.pack(fill="x", pady=(0 if index == 0 else self.theme.px(8),
                                                    self.theme.px(1)))
        if visible:
            if self.empty.winfo_manager():
                self.empty.pack_forget()
        else:
            if not self.empty.winfo_manager():
                self.empty.pack(fill="x", pady=(self.theme.px(8), self.theme.px(4)))
            if self._loaded:
                self.empty_title.configure(text="暂无正在运行的实验")
                self.empty_hint.configure(
                    text="有 CPU / GPU 占用的实验会自动显示在这里\n"
                         "悬停可看命令，右键可设置预计时长或结束实验")
            self._card_order = []
        self._hidden_count = hidden
        self._refresh_scroll()

    def _card_callbacks(self) -> dict:
        return {
            "open_folder": self.app.open_folder_for,
            "context_menu": self.app.show_context_menu,
            "tooltip": self.tooltip,
            "toggle_pause": self.app.toggle_pause,
            "edit_description": self.app.edit_description,
        }

    def _update_footer(self, snapshot: Snapshot) -> None:
        theme = self.theme
        parts = [f"每 {self._refresh_interval():g} 秒刷新"]
        if snapshot.history_count:
            parts.append(f"历史 {snapshot.history_count} 条")
        if snapshot.paused_experiments:
            parts.append(f"{snapshot.paused_experiments} 个已暂停")
        if self._hidden_count:
            parts.append(f"{self._hidden_count} 个空闲已折叠")
        if snapshot.demo:
            parts.append("演示模式")
        self.footer_left.configure(text=" · ".join(parts),
                                   fg=self.c["amber"] if snapshot.demo else self.c["muted"])
        self.footer_right.configure(text=fmt.fmt_clock(snapshot.ts))
        if snapshot.sampler_error:
            self.footer_right.configure(text="采样异常", fg=self.c["red"])

    def _refresh_interval(self) -> float:
        try:
            return float(self.config.get("sampling.ui_interval", 2.0))
        except Exception:
            return 2.0

    # ==================================================================
    # 滚动
    # ==================================================================
    def _on_cards_configure(self, event) -> None:
        try:
            self.cards_canvas.itemconfigure(self._cards_window, width=max(2, event.width))
        except Exception:
            pass
        self._refresh_scroll()

    def _on_mousewheel(self, event) -> None:
        if self.cards_inner.winfo_reqheight() <= self.cards_canvas.winfo_height() + 1:
            return
        step = int(event.delta / 120) * 2
        self.cards_canvas.yview_scroll(-step, "units")
        self._refresh_scroll()

    def _refresh_scroll(self) -> None:
        try:
            self.cards_canvas.update_idletasks()
            content_h = self.cards_inner.winfo_reqheight()
            self.cards_canvas.configure(
                scrollregion=(0, 0, self.cards_canvas.winfo_width(), content_h))
        except Exception:
            return
        try:
            self.scrollbar.update_view()
        except Exception:
            pass

    # ==================================================================
    # 拖拽窗口
    # ==================================================================
    def _bind_global(self) -> None:
        self.root.bind_all("<MouseWheel>", self._on_global_wheel, add="+")
        self.root.bind_all("<Escape>", self._on_escape, add="+")

    def _on_global_wheel(self, event) -> None:
        if self._collapsed:
            return
        try:
            widget = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget = None
        node = widget
        while node is not None:
            if node is self.cards_canvas or node is self.cards_inner:
                self._on_mousewheel(event)
                return
            try:
                node = node.master
            except Exception:
                return

    def _on_escape(self, _event=None) -> None:
        self.tooltip.hide()
        self.menu.close()

    def _bind_window_drag(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._drag_start, add="+")
        widget.bind("<B1-Motion>", self._drag_move, add="+")
        widget.bind("<ButtonRelease-1>", self._drag_end, add="+")

    def _drag_start(self, event) -> None:
        self._drag_anchor = (event.x_root - self.root.winfo_x(),
                             event.y_root - self.root.winfo_y())
        self._moved = False

    def _drag_move(self, event) -> None:
        if not self._drag_anchor:
            return
        x = event.x_root - self._drag_anchor[0]
        y = event.y_root - self._drag_anchor[1]
        vx, vy, vw, vh = winproc.virtual_screen_rect()
        x = max(vx - self.theme.px(20), min(x, vx + vw - self.theme.px(80)))
        y = max(vy, min(y, vy + vh - self.theme.px(40)))
        self.root.geometry(f"+{x}+{y}")
        self._moved = True

    def _drag_end(self, _event) -> None:
        if not self._moved:
            return
        self._moved = False
        try:
            self.app.save_window_position(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            pass

    # ==================================================================
    # 外部接口
    # ==================================================================
    def save_position(self) -> None:
        try:
            self.app.save_window_position(self.root.winfo_x(), self.root.winfo_y())
        except Exception:
            pass

    def flash_footer(self, text: str, color: Optional[str] = None, duration: int = 2400) -> None:
        """在底部状态栏短暂显示操作反馈。"""
        try:
            self.footer_left.configure(text=text, fg=color or self.c["cpu"])
        except Exception:
            return

        def restore() -> None:
            self._toast_job = None
            if self._snapshot is not None:
                try:
                    self._update_footer(self._snapshot)
                except Exception:
                    pass

        if self._toast_job:
            try:
                self.root.after_cancel(self._toast_job)
            except Exception:
                pass
        try:
            self._toast_job = self.root.after(duration, restore)
        except Exception:
            self._toast_job = None

    def toggle_collapsed_external(self) -> None:
        self._toggle_collapsed()

    def destroy(self) -> None:
        self.tooltip.hide()
        self.menu.close()
        try:
            self.pulse.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def mainloop(self) -> None:
        self.root.mainloop()

    def after(self, ms: int, func: Callable):
        return self.root.after(ms, func)
