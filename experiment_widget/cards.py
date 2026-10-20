"""实验卡片：按来源文件夹分组的实验行与指标展示。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from . import fmt
from .models import ExperimentView, FolderView
from .theme import Theme, folder_accent
from .widgets import ThinBar, draw_rounded_rect, ellipsize, ellipsize_left

_BASIS_COLORS = {
    "high": "green",
    "medium": "green",
    "low": "amber",
    "none": "muted",
}

_GPU_SOURCE_TEXT = {
    "pmon": "nvidia-smi pmon（进程 SM 利用率）",
    "engine": "Windows GPU 引擎计数器",
    "none": "无 GPU 活动",
}

_ICON_GLYPHS = {
    "pause": "❚❚",
    "play": "▶",
    "edit": "\uE70F",
}


def _glyph(theme: Theme, name: str) -> str:
    if name == "edit" and not theme.icon_family:
        return "✎"
    return _ICON_GLYPHS.get(name, "·")


def _eta_label(exp: ExperimentView) -> tuple[str, str]:
    """返回 (文案, 颜色 key)。"""
    if exp.paused:
        if exp.paused_seconds >= 60:
            return f"已暂停 {fmt.fmt_age_minutes(exp.paused_seconds)}", "cpu"
        return "已暂停", "cpu"
    estimate = exp.estimate
    if exp.idle and exp.idle_seconds >= 60 and (estimate is None or estimate.seconds is None):
        return f"空闲 {fmt.fmt_age_minutes(exp.idle_seconds)}", "muted"
    if estimate is not None and estimate.seconds is not None:
        color = _BASIS_COLORS.get(estimate.confidence, "green")
        return f"剩余 {fmt.fmt_eta(estimate.seconds)}", color
    return "剩余未知", "muted"


class ExperimentRow:
    """一个实验（含聚合的 worker 进程）的信息行。"""

    def __init__(self, parent: tk.Widget, theme: Theme, exp: ExperimentView,
                 callbacks: dict):
        self.theme = theme
        self.exp = exp
        self.callbacks = callbacks
        self._bg_widgets: list[tk.Widget] = []
        self.frame = tk.Frame(parent, bg=theme.c["card"])
        self.frame.pack(fill="x", pady=(theme.px(4), 0))

        top = tk.Frame(self.frame, bg=theme.c["card"])
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=theme.px(8), height=theme.px(8),
                             bg=theme.c["card"], highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, theme.px(7), theme.px(7),
                                           fill=theme.c["green"], outline="")

        # 右上操作：ETA ← 暂停/继续 ← 编辑描述（从右往左打包）
        self.eta = tk.Label(top, text="", font=theme.fonts["small_bold"],
                            bg=theme.c["card"], fg=theme.c["green"])
        self.eta.pack(side="right")
        self.pause_btn = self._action_button(
            top, _glyph(theme, "pause"), "暂停 / 继续实验（冻结进程，可恢复）",
            self._on_pause, use_icon_font=False)
        self.edit_btn = self._action_button(
            top, _glyph(theme, "edit"), "编辑实验描述", self._on_edit)
        self.dot.pack(side="left", padx=(0, theme.px(6)), pady=(theme.px(4), 0))
        self.name = tk.Label(top, text=exp.name, font=theme.fonts["name"],
                             bg=theme.c["card"], fg=theme.c["text"], anchor="w")
        self.name.pack(side="left", fill="x", expand=True)

        stats = tk.Frame(self.frame, bg=theme.c["card"])
        stats.pack(fill="x", pady=(theme.px(2), 0))
        # 右侧：显存 + 已运行（先 pack 右侧，保证不会被挤掉）
        self.stats_right = tk.Label(stats, text="", font=theme.fonts["tiny"],
                                    bg=theme.c["card"], fg=theme.c["muted"], anchor="e")
        self.stats_right.pack(side="right", pady=(theme.px(1), 0))
        # 框架小标签：PyTorch / TensorFlow ...
        self.framework_frame = tk.Frame(stats, bg=theme.c["card"])
        self.framework_frame.pack(side="left")
        self.framework_label = tk.Label(
            self.framework_frame, text="", font=theme.fonts["tiny"],
            bg=theme.c["card"], fg=theme.c["accent"], anchor="w")
        self.framework_label.pack(side="left")
        self._sep(self.framework_frame)
        self.cpu_key = self._mini(stats, "CPU", theme.c["muted"])
        self.cpu_value = self._mini(stats, "—", theme.c["cpu"], bold=True)
        self._sep(stats)
        self.gpu_key = self._mini(stats, "GPU", theme.c["muted"])
        self.gpu_value = self._mini(stats, "—", theme.c["gpu"], bold=True)

        # 想法 / 备注描述行
        self.desc_frame = tk.Frame(self.frame, bg=theme.c["card"])
        self.desc_tag = tk.Label(self.desc_frame, text="IDEA", font=theme.fonts["tiny"],
                                 bg=theme.c["card"], fg=theme.c["accent"])
        self.desc_tag.pack(side="left", padx=(0, theme.px(5)))
        self.desc_label = tk.Label(self.desc_frame, text="", font=theme.fonts["tiny"],
                                   bg=theme.c["card"], fg=theme.c["text2"], anchor="w")
        self.desc_label.pack(side="left", fill="x", expand=True)
        self._desc_shown = False
        self.desc_frame.bind("<Configure>", lambda e: self._update_desc_width(), add="+")

        self.progress_frame: Optional[tk.Frame] = None
        self.progress_bar: Optional[ThinBar] = None
        self.progress_label: Optional[tk.Label] = None

        self._bg_widgets.extend([
            self.frame, top, stats, self.name, self.eta,
            self.cpu_key, self.cpu_value, self.gpu_key, self.gpu_value,
            self.stats_right, self.framework_frame, self.framework_label,
            self.desc_frame, self.desc_tag, self.desc_label])
        self._hover_job: Optional[str] = None
        for widget in self._bg_widgets:
            widget.configure(cursor="arrow")
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
            widget.bind("<Double-Button-1>", self._on_double_click, add="+")
            widget.bind("<Button-3>", self._on_context, add="+")
        self._register_tooltip()
        self.top = top
        self._fit_job: Optional[str] = None
        top.bind("<Configure>", lambda e: self._schedule_fit(), add="+")
        self._update_desc_width()
        self.update(exp)

    # -- 构建辅助 -------------------------------------------------------
    def _mini(self, parent, text: str, color: str, bold: bool = False,
              family: Optional[str] = None) -> tk.Label:
        if family:
            font = self.theme.font(10.5, family=family)
        elif bold:
            font = self.theme.fonts["small_bold"]
        else:
            font = self.theme.fonts["small"]
        label = tk.Label(parent, text=text, font=font,
                         bg=self.theme.c["card"], fg=color, anchor="w")
        label.pack(side="left")
        return label

    def _sep(self, parent) -> None:
        tk.Label(parent, text="·", font=self.theme.fonts["small"],
                 bg=self.theme.c["card"], fg=self.theme.c["faint"]).pack(
            side="left", padx=self.theme.px(4))

    def _action_button(self, parent, glyph: str, tooltip: str, command,
                       use_icon_font: bool = True) -> tk.Label:
        theme = self.theme
        family = (theme.icon_family or theme.ui_family) if use_icon_font else theme.ui_family
        label = tk.Label(parent, text=glyph,
                         font=theme.font(11, family=family),
                         bg=theme.c["card"], fg=theme.c["muted"],
                         padx=theme.px(4), pady=theme.px(1), cursor="hand2")
        label.pack(side="right", padx=(0, theme.px(3)))
        label.bind("<Enter>", lambda e: (label.configure(fg=theme.c["text"]),
                                         self._on_enter()), add="+")
        label.bind("<Leave>", lambda e: (label.configure(
            fg=theme.c["muted"] if not self.exp.paused else theme.c["cpu"]),
            self._on_leave()), add="+")
        label.bind("<ButtonPress-1>", lambda e: command(), add="+")
        label._no_generic_tooltip = True  # type: ignore[attr-defined]
        tooltip_manager = self.callbacks.get("tooltip")
        if tooltip_manager is not None:
            tooltip_manager.attach(label, lambda: tooltip, delay=600)
        self._bg_widgets.append(label)
        return label

    def _on_pause(self) -> None:
        callback = self.callbacks.get("toggle_pause")
        if callback:
            callback(self.exp)

    def _on_edit(self) -> None:
        callback = self.callbacks.get("edit_description")
        if callback:
            callback(self.exp)

    def _update_desc_width(self) -> None:
        if not self._desc_shown:
            return
        theme = self.theme
        try:
            available = self.desc_frame.winfo_width() - self.desc_tag.winfo_reqwidth() - theme.px(10)
        except Exception:
            return
        text = self.exp.idea or self.exp.note
        self.desc_label.configure(text=ellipsize(text, theme.fonts["tiny"], max(60, available)))

    def _register_tooltip(self) -> None:
        tooltip = self.callbacks.get("tooltip")
        if tooltip is None:
            return

        def provider() -> str:
            return self.tooltip_text(self.exp)

        for widget in self._bg_widgets:
            if getattr(widget, "_no_generic_tooltip", False):
                continue
            tooltip.attach(widget, provider, delay=550)

    # -- 悬停 -----------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        if self._hover_job:
            try:
                self.frame.after_cancel(self._hover_job)
            except Exception:
                pass
            self._hover_job = None
        self._recolor(self.theme.c["card_hover"])

    def _on_leave(self, _event=None) -> None:
        self._hover_job = self.frame.after(60, self._maybe_leave)

    def _maybe_leave(self) -> None:
        self._hover_job = None
        try:
            pointer = self.frame.winfo_pointerxy()
            widget = self.frame.winfo_containing(*pointer)
        except Exception:
            widget = None
        node = widget
        while node is not None:
            if node is self.frame:
                return
            try:
                node = node.master
            except Exception:
                return
        self._recolor(self.theme.c["card"])

    def _recolor(self, color: str) -> None:
        for widget in self._bg_widgets:
            try:
                widget.configure(bg=color)
            except Exception:
                pass
        if self.progress_frame is not None:
            try:
                self.progress_frame.configure(bg=color)
                self.progress_bar.configure(bg=color)
                self.progress_label.configure(bg=color)
            except Exception:
                pass

    # -- 交互 -----------------------------------------------------------
    def _on_double_click(self, _event=None) -> None:
        callback = self.callbacks.get("open_folder")
        if callback:
            callback(self.exp)

    def _on_context(self, event) -> None:
        callback = self.callbacks.get("context_menu")
        if callback:
            callback(self.exp, event.x_root, event.y_root)

    # -- 更新 -----------------------------------------------------------
    def _schedule_fit(self) -> None:
        if self._fit_job:
            return
        self._fit_job = self.frame.after(30, self._fit_name)

    def _fit_name(self) -> None:
        self._fit_job = None
        theme = self.theme
        try:
            available = (self.top.winfo_width() - self.eta.winfo_reqwidth()
                         - self.pause_btn.winfo_reqwidth()
                         - self.edit_btn.winfo_reqwidth()
                         - self.dot.winfo_reqwidth() - theme.px(12))
        except Exception:
            return
        self.name.configure(text=ellipsize(self.exp.name, theme.fonts["name"],
                                           max(40, available)))

    def update(self, exp: ExperimentView) -> None:
        self.exp = exp
        theme = self.theme
        eta_text, eta_color = _eta_label(exp)
        self.eta.configure(text=eta_text, fg=theme.c[eta_color])
        self._schedule_fit()
        self.cpu_value.configure(text=fmt.fmt_cpu_pct(exp.cpu_pct))
        gpu_text = fmt.fmt_pct(exp.gpu_util) if exp.gpu_util is not None else "—"
        self.gpu_value.configure(text=gpu_text)
        right: list[str] = []
        if exp.gpu_mem_mb and exp.gpu_mem_mb >= 1:
            right.append(f"显存 {fmt.fmt_mb_short(exp.gpu_mem_mb)}")
        right.append(fmt.fmt_elapsed(exp.elapsed))
        self.stats_right.configure(text=" · ".join(right))

        # 暂停 / 继续 按钮状态
        self.pause_btn.configure(
            text=_glyph(theme, "play" if exp.paused else "pause"),
            fg=theme.c["cpu"] if exp.paused else theme.c["muted"])

        # 框架标签
        framework = (exp.framework or "").strip()
        if framework:
            self.framework_label.configure(
                text=ellipsize(framework, theme.fonts["tiny"], theme.px(120)))
            if not self.framework_frame.winfo_manager():
                self.framework_frame.pack(side="left", before=self.cpu_key)
        elif self.framework_frame.winfo_manager():
            self.framework_frame.pack_forget()

        # 想法 / 备注描述行
        desc_text = (exp.idea or exp.note or "").strip()
        if desc_text:
            self.desc_tag.configure(text="IDEA" if exp.idea else "备注",
                                    fg=theme.c["accent"] if exp.idea else theme.c["muted"])
            if not self._desc_shown:
                if self.progress_frame is not None:
                    self.desc_frame.pack(fill="x", pady=(theme.px(3), 0),
                                         before=self.progress_frame)
                else:
                    self.desc_frame.pack(fill="x", pady=(theme.px(3), 0))
                self._desc_shown = True
            self.desc_frame.after_idle(self._update_desc_width)
        elif self._desc_shown:
            self.desc_frame.pack_forget()
            self._desc_shown = False

        if exp.paused:
            dot_color = theme.c["cpu"]
            self.name.configure(fg=theme.c["text2"])
        elif exp.idle and exp.idle_seconds >= 30:
            dot_color = theme.c["amber"]
            self.name.configure(fg=theme.c["text2"])
        else:
            dot_color = theme.c["green"]
            self.name.configure(fg=theme.c["text"])
        self.dot.itemconfigure(self.dot_id, fill=dot_color)

        progress = exp.progress
        if progress is not None:
            self._ensure_progress_row()
            self.progress_bar.set(progress.pct,
                                  theme.c["amber"] if progress.stale else theme.c["green"])
            label = progress.label
            pct_text = f"{progress.pct:.1f}%"
            if label:
                self.progress_label.configure(text=f"{label} · {pct_text}")
            else:
                self.progress_label.configure(text=pct_text)
            if progress.stale:
                self.progress_label.configure(fg=theme.c["amber"])
            else:
                self.progress_label.configure(fg=theme.c["muted"])
        elif self.progress_frame is not None:
            self.progress_frame.destroy()
            self.progress_frame = None
            self.progress_bar = None
            self.progress_label = None

    def _ensure_progress_row(self) -> None:
        if self.progress_frame is not None:
            return
        theme = self.theme
        frame = tk.Frame(self.frame, bg=theme.c["card"])
        frame.pack(fill="x", pady=(theme.px(4), 0))
        bar = ThinBar(frame, theme, height=3, track=theme.c["track"])
        bar.configure(highlightthickness=0)
        bar.pack(side="left", fill="x", expand=True, pady=(theme.px(2), 0))
        label = tk.Label(frame, text="", font=theme.fonts["tiny"],
                         bg=theme.c["card"], fg=theme.c["muted"])
        label.pack(side="right", padx=(theme.px(6), 0))
        self.progress_frame = frame
        self.progress_bar = bar
        self.progress_label = label
        self._bg_widgets.extend([frame, label])
        for widget in (frame, label):
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
            widget.bind("<Double-Button-1>", self._on_double_click, add="+")
            widget.bind("<Button-3>", self._on_context, add="+")
        tooltip = self.callbacks.get("tooltip")
        if tooltip is not None:
            for widget in (frame, bar, label):
                tooltip.attach(widget, lambda: self.tooltip_text(self.exp), delay=550)

    @staticmethod
    def tooltip_text(exp: ExperimentView) -> str:
        lines = [f"{exp.name}"]
        lines.append(f"文件夹　{exp.folder}（来源：{exp.folder_source}）")
        if exp.framework:
            lines.append(f"框架　　{exp.framework}")
        if exp.idea:
            lines.append(f"想法　　{exp.idea}")
        if exp.note:
            lines.append(f"备注　　{exp.note}")
        if exp.paused:
            state = f"已暂停 {fmt.fmt_elapsed_long(exp.paused_seconds)}（进程已冻结，可继续）"
        else:
            state = "空闲" if exp.idle and exp.idle_seconds >= 30 else "运行中"
        lines.append(f"状态　　{state} · 已运行 {fmt.fmt_elapsed_long(exp.elapsed)}")
        lines.append(f"CPU　　 {fmt.fmt_cpu_pct(exp.cpu_pct)}（占整机）")
        gpu_text = fmt.fmt_pct(exp.gpu_util) if exp.gpu_util is not None else "—"
        gpu_source = _GPU_SOURCE_TEXT.get(exp.gpu_source, exp.gpu_source)
        lines.append(f"GPU　　 {gpu_text} · {gpu_source}")
        if exp.gpu_mem_mb:
            lines.append(f"显存　　{fmt.fmt_mb(exp.gpu_mem_mb)}")
        estimate = exp.estimate
        if estimate is not None:
            if estimate.seconds is not None:
                lines.append(f"预计剩余　{fmt.fmt_eta(estimate.seconds)}（{estimate.basis_label}）")
            else:
                lines.append("预计剩余　未知")
            if estimate.detail:
                lines.append(f"估算依据　{estimate.detail}")
        if exp.manual_total:
            lines.append(f"手动总时长　{fmt.fmt_elapsed_long(exp.manual_total)}")
        if exp.log_path:
            lines.append(f"进度日志　{exp.log_path}")
        lines.append(f"PID　　　{', '.join(str(pid) for pid in exp.pids[:6])}"
                     + (" …" if len(exp.pids) > 6 else ""))
        if exp.cmdline:
            command = exp.cmdline if len(exp.cmdline) <= 500 else exp.cmdline[:499] + "…"
            lines.append(f"命令　　{command}")
        lines.append("提示　　点 ⏸ 可安全暂停 / 继续；点 ✎ 或右键可编辑描述；"
                     "右键还有预计时长、日志、结束实验")
        return "\n".join(lines)


class FolderCard:
    """一个来源文件夹的卡片，内含若干实验行。"""

    def __init__(self, parent: tk.Widget, theme: Theme, view: FolderView,
                 callbacks: dict):
        self.theme = theme
        self.callbacks = callbacks
        self.view = view
        self.accent_color = folder_accent(view.folder)
        self.frame = tk.Frame(parent, bg=theme.c["card"],
                              highlightbackground=theme.c["border"],
                              highlightthickness=1)
        self.accent = tk.Frame(self.frame, bg=self.accent_color, width=theme.px(3))
        self.accent.pack(side="left", fill="y")
        self.accent.pack_propagate(False)
        self.body = tk.Frame(self.frame, bg=theme.c["card"],
                             padx=theme.px(10), pady=theme.px(8))
        self.body.pack(side="left", fill="both", expand=True)

        header = tk.Frame(self.body, bg=theme.c["card"])
        header.pack(fill="x")
        self.count = tk.Label(header, text="", font=theme.fonts["tiny"],
                              bg=theme.c["card"], fg=theme.c["muted"])
        self.count.pack(side="right")
        icon_glyph = "\uE8B7" if theme.icon_family else "▣"
        self.folder_icon = tk.Label(
            header, text=icon_glyph,
            font=theme.font(12, family=theme.icon_family or theme.ui_family),
            bg=theme.c["card"], fg=self.accent_color)
        self.folder_icon.pack(side="left", padx=(0, theme.px(6)))
        self.title = tk.Label(header, text=view.display, font=theme.fonts["section"],
                              bg=theme.c["card"], fg=theme.c["text"], anchor="w")
        self.title.pack(side="left", fill="x", expand=True)

        meta = tk.Frame(self.body, bg=theme.c["card"])
        meta.pack(fill="x", pady=(theme.px(1), theme.px(2)))
        self.aggregate = tk.Label(meta, text="", font=theme.fonts["tiny"],
                                  bg=theme.c["card"], fg=theme.c["muted"])
        self.aggregate.pack(side="right")
        self.path_label = tk.Label(meta, text=view.folder, font=theme.fonts["tiny"],
                                   bg=theme.c["card"], fg=theme.c["faint"], anchor="w")
        self.path_label.pack(side="left", fill="x", expand=True)

        self.rows_frame = tk.Frame(self.body, bg=theme.c["card"])
        self.rows_frame.pack(fill="x")
        self.rows: dict[str, ExperimentRow] = {}
        self._path_job: Optional[str] = None
        self.body.bind("<Configure>", lambda e: self._schedule_path_layout(), add="+")
        self._folder_tooltip()

        self.update(view)

    def _schedule_path_layout(self) -> None:
        if self._path_job:
            return
        self._path_job = self.frame.after(30, self._relayout_path)

    def _folder_tooltip(self) -> None:
        tooltip = self.callbacks.get("tooltip")
        if tooltip is None:
            return

        def provider() -> str:
            lines = [
                f"文件夹　{self.view.folder}",
                f"实验数量　{self.view.count}",
                f"CPU 合计　{fmt.fmt_cpu_pct(self.view.cpu_pct)}（占整机）",
            ]
            if self.view.gpu_util is not None:
                lines.append(f"GPU 合计　{fmt.fmt_pct(self.view.gpu_util)}")
            if self.view.gpu_mem_mb:
                lines.append(f"显存合计　{fmt.fmt_mb(self.view.gpu_mem_mb)}")
            lines.append("双击实验行可在资源管理器中打开文件夹")
            return "\n".join(lines)

        for widget in (self.title, self.path_label, self.count, self.aggregate,
                       self.folder_icon):
            tooltip.attach(widget, provider, delay=600)

    # -- 更新 -----------------------------------------------------------
    def update(self, view: FolderView) -> None:
        self.view = view
        self.title.configure(text=view.display)
        self.count.configure(text=f"{view.count} 个实验")
        parts = [f"CPU {fmt.fmt_cpu_pct(view.cpu_pct)}"]
        if view.gpu_util is not None and view.gpu_util > 0:
            parts.append(f"GPU {fmt.fmt_pct(view.gpu_util)}")
        if view.gpu_mem_mb and view.gpu_mem_mb >= 1:
            parts.append(f"显存 {fmt.fmt_mb_short(view.gpu_mem_mb)}")
        self.aggregate.configure(text=" · ".join(parts))
        visible = [exp for exp in view.experiments if not exp.hidden]
        keys = [exp.key for exp in visible]
        if keys != list(self.rows.keys()):
            self._rebuild_rows(visible)
        else:
            for exp in visible:
                self.rows[exp.key].update(exp)
        self._relayout_path()

    def _rebuild_rows(self, experiments: list[ExperimentView]) -> None:
        for row in self.rows.values():
            row.frame.destroy()
        self.rows.clear()
        for exp in experiments:
            row = ExperimentRow(self.rows_frame, self.theme, exp, self.callbacks)
            self.rows[exp.key] = row

    def _relayout_path(self) -> None:
        self._path_job = None
        theme = self.theme
        try:
            total = self.body.winfo_width() - theme.px(2)
            aggregate_w = self.aggregate.winfo_reqwidth()
            avail = total - aggregate_w - theme.px(8)
            self.path_label.configure(
                text=ellipsize_left(self.view.folder, theme.fonts["tiny"], max(40, avail)))
        except Exception:
            pass

    def destroy(self) -> None:
        self.frame.destroy()
