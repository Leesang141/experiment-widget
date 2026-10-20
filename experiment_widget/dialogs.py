"""设置 / 输入 / 确认等深色弹窗。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from . import fmt
from .config import Config
from .theme import Theme
from .widgets import TextButton


def _center_over(parent: tk.Misc, width: int, height: int) -> tuple[int, int]:
    try:
        parent.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
    except Exception:
        px = py = 0
        pw, ph = 1280, 800
    screen_w = parent.winfo_screenwidth()
    screen_h = parent.winfo_screenheight()
    x = min(max(8, px + (pw - width) // 2), screen_w - width - 8)
    y = min(max(8, py + (ph - height) // 2), screen_h - height - 8)
    return x, y


class ModalBase:
    def __init__(self, parent: tk.Misc, theme: Theme, width_design: int = 330,
                 title: str = ""):
        self.parent = parent
        self.theme = theme
        self.result = None
        self.win = tk.Toplevel(parent)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=theme.c["border"])
        self.width = theme.px(width_design)
        self.outer = tk.Frame(self.win, bg=theme.c["panel"], padx=theme.px(16),
                              pady=theme.px(14))
        self.outer.pack(fill="both", expand=True, padx=theme.px(1), pady=theme.px(1))
        if title:
            header = tk.Frame(self.outer, bg=theme.c["panel"])
            header.pack(fill="x", pady=(0, theme.px(10)))
            tk.Label(header, text=title, font=theme.fonts["section"],
                     bg=theme.c["panel"], fg=theme.c["text"], anchor="w").pack(
                side="left")
            close = tk.Label(header, text="✕", font=theme.fonts["small"],
                             bg=theme.c["panel"], fg=theme.c["muted"],
                             padx=theme.px(6), cursor="hand2")
            close.pack(side="right")
            close.bind("<ButtonRelease-1>", lambda e: self.close())
        self.win.bind("<Escape>", lambda e: self.close())
        self._drag_anchor = None
        for widget in (self.outer,):
            widget.bind("<ButtonPress-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_move, add="+")

    # -- 拖动 -----------------------------------------------------------
    def _drag_start(self, event) -> None:
        self._drag_anchor = (event.x_root - self.win.winfo_rootx(),
                             event.y_root - self.win.winfo_rooty())

    def _drag_move(self, event) -> None:
        if not self._drag_anchor:
            return
        x = event.x_root - self._drag_anchor[0]
        y = event.y_root - self._drag_anchor[1]
        self.win.geometry(f"+{x}+{y}")

    # -- 显示 / 关闭 -----------------------------------------------------
    def show(self) -> None:
        self.win.update_idletasks()
        width = self.width
        height = self.win.winfo_reqheight()
        x, y = _center_over(self.parent, width, height)
        self.win.geometry(f"{width}x{height}+{x}+{y}")
        self.win.deiconify()
        self.win.lift()
        try:
            self.win.grab_set()
        except Exception:
            pass
        self.win.focus_force()

    def close(self) -> None:
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass

    def wait(self):
        try:
            self.parent.wait_window(self.win)
        except Exception:
            pass
        return self.result


class PromptDurationDialog(ModalBase):
    """设置预计总时长。"""

    def __init__(self, parent, theme: Theme, title: str, message: str,
                 initial_seconds: Optional[float], on_clear: Optional[Callable[[], None]] = None):
        super().__init__(parent, theme, width_design=330, title=title)
        self._on_clear = on_clear
        tk.Label(self.outer, text=message, font=self.theme.fonts["small"],
                 bg=self.theme.c["panel"], fg=self.theme.c["text2"], justify="left",
                 anchor="w", wraplength=self.width - theme.px(40)).pack(
            fill="x", pady=(0, theme.px(8)))
        entry_bg = self.theme.c["card"]
        self.entry = tk.Entry(
            self.outer, font=self.theme.fonts["body"], bg=entry_bg,
            fg=self.theme.c["text"], insertbackground=self.theme.c["cpu"],
            relief="flat", highlightthickness=1,
            highlightbackground=self.theme.c["border"],
            highlightcolor=self.theme.c["cpu"],
        )
        initial = ""
        if initial_seconds:
            if initial_seconds >= 3600 and initial_seconds % 3600 == 0:
                initial = f"{initial_seconds / 3600:g}h"
            elif initial_seconds >= 60 and initial_seconds % 60 == 0:
                initial = f"{initial_seconds / 60:g}m"
            else:
                initial = str(int(initial_seconds))
        self.entry.insert(0, initial)
        self.entry.pack(fill="x", ipady=self.theme.px(4), pady=(0, theme.px(4)))
        hint = "支持 90 / 90m / 1.5h / 2h30m / 1:30（纯数字按分钟）"
        tk.Label(self.outer, text=hint, font=self.theme.fonts["tiny"],
                 bg=self.theme.c["panel"], fg=self.theme.c["faint"], anchor="w").pack(
            fill="x")
        self.error = tk.Label(self.outer, text="", font=self.theme.fonts["tiny"],
                              bg=self.theme.c["panel"], fg=self.theme.c["red"], anchor="w")
        self.error.pack(fill="x", pady=(theme.px(2), 0))
        buttons = tk.Frame(self.outer, bg=self.theme.c["panel"])
        buttons.pack(fill="x", pady=(theme.px(12), 0))
        if on_clear is not None:
            TextButton(buttons, theme, "清除设定", self._clear, kind="ghost").pack(side="left")
        TextButton(buttons, theme, "取消", self.close, kind="ghost").pack(side="right")
        TextButton(buttons, theme, "确定", self._ok, kind="primary").pack(
            side="right", padx=(0, theme.px(8)))
        self.entry.bind("<Return>", lambda e: self._ok())
        self.entry.focus_set()

        # 点击外层空白也能聚焦输入框
        self.outer.bind("<Button-1>", lambda e: self.entry.focus_set(), add="+")

    def _ok(self) -> None:
        text = self.entry.get().strip()
        if not text:
            self.error.configure(text="请输入时长，或点击“清除设定”")
            return
        seconds = fmt.parse_duration_text(text)
        if not seconds or seconds <= 0:
            self.error.configure(text="格式无法识别，试试 90m / 1.5h / 1:30")
            return
        self.result = seconds
        self.close()

    def _clear(self) -> None:
        self.result = "clear"
        self.close()


class DescriptionDialog(ModalBase):
    """编辑实验描述：想法 / 框架 / 备注。"""

    def __init__(self, parent, theme: Theme, exp, auto_hint: str = "",
                 project_hint: str = ""):
        super().__init__(parent, theme, width_design=360, title="实验描述")
        self.exp = exp
        outer = self.outer

        tk.Label(outer, text=f"文件夹　{exp.folder}", font=self.theme.fonts["tiny"],
                 bg=theme.c["panel"], fg=theme.c["faint"], anchor="w",
                 wraplength=self.width - theme.px(36)).pack(fill="x")
        if auto_hint:
            tk.Label(outer, text=f"识别　{auto_hint}", font=theme.fonts["tiny"],
                     bg=theme.c["panel"], fg=theme.c["text2"], anchor="w").pack(
                fill="x", pady=(theme.px(2), 0))
        if project_hint:
            tk.Label(outer, text=project_hint, font=theme.fonts["tiny"],
                     bg=theme.c["panel"], fg=theme.c["muted"], anchor="w").pack(
                fill="x", pady=(theme.px(1), 0))
        tk.Frame(outer, bg=theme.c["border_soft"], height=1).pack(
            fill="x", pady=theme.px(9))

        self.idea_entry = self._add_entry(outer, "想法 / 候选",
                                          "例如：候选10 · 补 8 下界实验",
                                          exp.idea)
        self.framework_entry = self._add_entry(outer, "框架",
                                               "例如：PyTorch / TensorFlow",
                                               exp.framework)
        tk.Label(outer, text="备注（可选）", font=theme.fonts["tiny"],
                 bg=theme.c["panel"], fg=theme.c["muted"], anchor="w").pack(
            fill="x", pady=(theme.px(7), theme.px(3)))
        self.note_text = tk.Text(
            outer, height=3, font=theme.fonts["small"], bg=theme.c["card"],
            fg=theme.c["text"], insertbackground=theme.c["cpu"], relief="flat",
            highlightthickness=1, highlightbackground=theme.c["border"],
            highlightcolor=theme.c["cpu"], wrap="word", padx=theme.px(7),
            pady=theme.px(5),
        )
        self.note_text.pack(fill="x")
        if exp.note:
            self.note_text.insert("1.0", exp.note)

        tk.Label(
            outer,
            text="保存后按“文件夹 + 脚本”记住描述；重新启动同一个实验会自动沿用。\n"
                 "也可以在同目录放 EXPERIMENT.md / .experiment.json 自动提供想法。",
            font=theme.fonts["tiny"], bg=theme.c["panel"], fg=theme.c["faint"],
            anchor="w", justify="left",
            wraplength=self.width - theme.px(36),
        ).pack(fill="x", pady=(theme.px(7), 0))

        buttons = tk.Frame(outer, bg=theme.c["panel"])
        buttons.pack(fill="x", pady=(theme.px(12), 0))
        TextButton(buttons, theme, "清除描述", self._clear, kind="ghost").pack(side="left")
        TextButton(buttons, theme, "取消", self.close, kind="ghost").pack(side="right")
        TextButton(buttons, theme, "保存", self._save, kind="primary").pack(
            side="right", padx=(0, theme.px(8)))
        self.idea_entry.bind("<Return>", lambda e: self._save())
        self.idea_entry.focus_set()

    def _add_entry(self, parent, label: str, placeholder: str,
                   value: str) -> tk.Entry:
        theme = self.theme
        tk.Label(parent, text=label, font=theme.fonts["tiny"],
                 bg=theme.c["panel"], fg=theme.c["muted"], anchor="w").pack(
            fill="x", pady=(theme.px(6), theme.px(3)))
        entry = tk.Entry(
            parent, font=theme.fonts["small"], bg=theme.c["card"],
            fg=theme.c["text"], insertbackground=theme.c["cpu"], relief="flat",
            highlightthickness=1, highlightbackground=theme.c["border"],
            highlightcolor=theme.c["cpu"],
        )
        entry.pack(fill="x", ipady=theme.px(4))
        if value:
            entry.insert(0, value)
        else:
            entry.insert(0, "")
        entry.configure(disabledforeground=theme.c["faint"])
        return entry

    def _save(self) -> None:
        self.result = {
            "idea": self.idea_entry.get().strip(),
            "framework": self.framework_entry.get().strip(),
            "note": self.note_text.get("1.0", "end").strip(),
        }
        self.close()

    def _clear(self) -> None:
        self.result = "clear"
        self.close()


class ConfirmDialog(ModalBase):
    def __init__(self, parent, theme: Theme, title: str, message: str,
                 ok_text: str = "确定", danger: bool = False):
        super().__init__(parent, theme, width_design=330, title=title)
        tk.Label(self.outer, text=message, font=self.theme.fonts["small"],
                 bg=self.theme.c["panel"], fg=self.theme.c["text2"], justify="left",
                 anchor="w", wraplength=self.width - theme.px(40)).pack(
            fill="x", pady=(0, theme.px(12)))
        buttons = tk.Frame(self.outer, bg=self.theme.c["panel"])
        buttons.pack(fill="x")
        TextButton(buttons, theme, "取消", self.close, kind="ghost").pack(side="right")
        TextButton(buttons, theme, ok_text, self._ok,
                   kind="danger" if danger else "primary").pack(
            side="right", padx=(0, theme.px(8)))

    def _ok(self) -> None:
        self.result = True
        self.close()


class Toggle(tk.Frame):
    def __init__(self, parent, theme: Theme, value: bool, command: Callable[[bool], None]):
        super().__init__(parent, bg=theme.c["panel"])
        self.theme = theme
        self.value = bool(value)
        self.command = command
        self.label = tk.Label(self, font=theme.fonts["small_bold"], padx=theme.px(10),
                              pady=theme.px(3), cursor="hand2")
        self.label.pack()
        self.label.bind("<ButtonRelease-1>", self._toggle)
        self._render()

    def _render(self) -> None:
        if self.value:
            self.label.configure(text="开启", bg=self.theme.c["green_dim"],
                                 fg=self.theme.c["green"])
        else:
            self.label.configure(text="关闭", bg=self.theme.c["card"],
                                 fg=self.theme.c["muted"])

    def _toggle(self, _event=None) -> None:
        self.value = not self.value
        self._render()
        self.command(self.value)


class Segmented(tk.Frame):
    def __init__(self, parent, theme: Theme, options: list[tuple[str, object]],
                 current: object, command: Callable[[object], None]):
        super().__init__(parent, bg=theme.c["panel"])
        self.theme = theme
        self.command = command
        self.options = options
        self.labels: list[tk.Label] = []
        for text, value in options:
            label = tk.Label(self, text=text, font=theme.fonts["tiny"],
                             padx=theme.px(7), pady=theme.px(3), cursor="hand2")
            label.pack(side="left", padx=(0, theme.px(2)))
            label.bind("<ButtonRelease-1>", lambda e, v=value: self._select(v))
            self.labels.append(label)
        self.current = current
        self._render()

    def _select(self, value) -> None:
        self.current = value
        self._render()
        self.command(value)

    def _render(self) -> None:
        for (text, value), label in zip(self.options, self.labels):
            if value == self.current:
                label.configure(bg=self.theme.c["cpu_dim"], fg=self.theme.c["cpu"])
            else:
                label.configure(bg=self.theme.c["card"], fg=self.theme.c["muted"])

    def set_value(self, value) -> None:
        self.current = value
        self._render()


class SettingsDialog(ModalBase):
    def __init__(self, parent, theme: Theme, app):
        super().__init__(parent, theme, width_design=360, title="设置")
        self.app = app
        self.config: Config = app.config
        body = tk.Frame(self.outer, bg=theme.c["panel"])
        body.pack(fill="both", expand=True)
        self._rows = 0

        self._add_row(body, "刷新间隔", lambda parent: Segmented(
            parent, theme,
            [("1 秒", 1.0), ("2 秒", 2.0), ("3 秒", 3.0), ("5 秒", 5.0)],
            float(self.config.get("sampling.ui_interval", 2.0)),
            self._set_interval))
        self._add_row(body, "窗口置顶", lambda parent: Toggle(
            parent, theme, bool(self.config.get("window.always_on_top", True)),
            self._set_topmost))
        self._add_row(body, "不透明度", lambda parent: Segmented(
            parent, theme,
            [("100%", 1.0), ("98%", 0.98), ("95%", 0.95)],
            float(self.config.get("window.opacity", 0.98)),
            self._set_opacity))
        self._add_row(body, "空闲实验隐藏", lambda parent: Segmented(
            parent, theme,
            [("30 分", 30), ("1 时", 60), ("90 分", 90), ("不隐藏", 0)],
            int(self.config.get("detect.idle_hide_minutes", 90)),
            self._set_idle_hide))
        self._add_row(body, "历史运行估算", lambda parent: Toggle(
            parent, theme, bool(self.config.get("estimate.history_enabled", True)),
            self._set_history))
        self._add_row(body, "读取进度日志", lambda parent: Toggle(
            parent, theme, bool(self.config.get("estimate.progress_enabled", True)),
            self._set_progress))
        self._add_row(body, "灵敏度", lambda parent: Segmented(
            parent, theme,
            [("低", 0), ("标准", 1), ("高", 2)],
            self._sensitivity_value(),
            self._set_sensitivity))

        actions = tk.Frame(self.outer, bg=theme.c["panel"])
        actions.pack(fill="x", pady=(theme.px(14), 0))
        TextButton(actions, theme, "清空历史", self._clear_history, kind="ghost").pack(side="left")
        TextButton(actions, theme, "打开数据目录", self._open_data, kind="ghost").pack(
            side="left", padx=(theme.px(6), 0))
        TextButton(actions, theme, "完成", self.close, kind="primary").pack(side="right")

        footer = tk.Label(
            self.outer,
            text="所有数据仅保存在本机 · 配置文件 data/config.json",
            font=theme.fonts["tiny"], bg=theme.c["panel"], fg=theme.c["faint"],
            anchor="w",
        )
        footer.pack(fill="x", pady=(theme.px(10), 0))

    def _add_row(self, parent, label: str, factory) -> None:
        theme = self.theme
        if self._rows:
            tk.Frame(parent, bg=theme.c["border_soft"], height=1).pack(
                fill="x", pady=theme.px(5))
        row = tk.Frame(parent, bg=theme.c["panel"])
        row.pack(fill="x")
        tk.Label(row, text=label, font=theme.fonts["small"],
                 bg=theme.c["panel"], fg=theme.c["text2"], anchor="w").pack(
            side="left", fill="x", expand=True)
        control = factory(row)
        control.pack(side="right")
        self._rows += 1

    # -- 设置回调 -------------------------------------------------------
    def _set_interval(self, value: float) -> None:
        self.app.apply_setting("sampling.ui_interval", float(value))

    def _set_topmost(self, value: bool) -> None:
        self.app.apply_setting("window.always_on_top", bool(value))

    def _set_opacity(self, value: float) -> None:
        self.app.apply_setting("window.opacity", float(value))

    def _set_idle_hide(self, value: int) -> None:
        self.app.apply_setting("detect.idle_hide_minutes", int(value))

    def _set_history(self, value: bool) -> None:
        self.app.apply_setting("estimate.history_enabled", bool(value))

    def _set_progress(self, value: bool) -> None:
        self.app.apply_setting("estimate.progress_enabled", bool(value))

    def _sensitivity_value(self) -> int:
        min_cpu = float(self.config.get("detect.min_cpu_pct", 2.0))
        if min_cpu <= 1.0:
            return 2
        if min_cpu >= 4.0:
            return 0
        return 1

    def _set_sensitivity(self, value: int) -> None:
        mapping = {
            0: (4.0, 5.0),
            1: (2.0, 3.0),
            2: (1.0, 1.5),
        }
        cpu, gpu = mapping.get(int(value), (2.0, 3.0))
        self.app.apply_setting("detect.min_cpu_pct", cpu)
        self.app.apply_setting("detect.min_gpu_util", gpu)

    def _clear_history(self) -> None:
        self.app.clear_history()
        self.win.lift()

    def _open_data(self) -> None:
        self.app.open_data_dir()
        self.win.lift()
