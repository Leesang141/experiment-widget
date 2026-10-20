"""应用控制器：数据源调度、交互命令、设置与退出。"""

from __future__ import annotations

import argparse
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox

from . import fmt, winproc
from .config import DATA_DIR, Config, open_in_explorer
from .demo import DemoSource
from .dialogs import ConfirmDialog, DescriptionDialog, PromptDurationDialog, SettingsDialog
from .models import ExperimentView, Snapshot
from .sampler import Sampler
from .ui import MainWindow
from .util import log_line

SINGLE_INSTANCE_NAME = "Local\\ExperimentWidget-SingleInstance"
APP_TITLE = "实验监控"

_DISTRIBUTED_TOKENS = (
    "torchrun", "accelerate launch", "deepspeed", "mpirun", "mpiexec",
    "nccl", "distributed", "nproc_per_node", "srun", "horovod",
)


class App:
    def __init__(self, demo: bool = False):
        self.demo = demo
        self.config = Config()
        self._stopped = False
        self._quitting = False
        self._last_ui_ts = 0.0
        self._settings_dialog = None
        self._last_snapshot: Snapshot | None = None
        self._demo_source: DemoSource | None = DemoSource() if demo else None
        self.sampler: Sampler | None = None
        self.window = MainWindow(self, self.config)
        if not demo:
            self.sampler = Sampler(self.config)
            self.sampler.start()
            log_line("sampler started")
        self.window.after(150, self._poll)
        self._demo_started = time.monotonic()

    # ==================================================================
    # 主循环
    # ==================================================================
    def run(self) -> None:
        try:
            self.window.mainloop()
        finally:
            self.shutdown()

    def _poll(self) -> None:
        if self._stopped:
            return
        snapshot = None
        try:
            if self._demo_source is not None:
                snapshot = self._demo_source.poll_latest()
            elif self.sampler is not None:
                snapshot = self.sampler.poll_latest()
        except Exception as exc:  # pragma: no cover
            log_line(f"poll error: {exc}")
        now = time.monotonic()
        interval = self._ui_interval()
        if snapshot is not None:
            self._last_snapshot = snapshot
            if now - self._last_ui_ts >= max(0.2, interval - 0.08):
                self._last_ui_ts = now
                try:
                    self.window.update_snapshot(snapshot)
                except Exception as exc:  # pragma: no cover
                    log_line(f"ui update error: {exc}")
        self.window.after(120, self._poll)

    def _ui_interval(self) -> float:
        try:
            return float(self.config.get("sampling.ui_interval", 2.0))
        except (TypeError, ValueError):
            return 2.0

    # ==================================================================
    # 配置 / 窗口
    # ==================================================================
    def apply_setting(self, key: str, value) -> None:
        self.config.set(key, value, save=True)
        log_line(f"setting {key}={value!r}")
        if key == "window.always_on_top":
            self.window.set_topmost(bool(value))
        elif key == "window.opacity":
            self.window.set_opacity(float(value))
        elif key == "detect.idle_hide_minutes" and self._last_snapshot is not None:
            pass
        elif key == "sampling.ui_interval":
            self._last_ui_ts = 0.0

    def save_window_position(self, x: int, y: int) -> None:
        self.config.set("window.x", int(x))
        self.config.set("window.y", int(y))
        self.config.save()

    def open_settings(self) -> None:
        try:
            if self._settings_dialog is not None and self._settings_dialog.win.winfo_exists():
                self._settings_dialog.win.lift()
                return
        except Exception:
            pass
        self._settings_dialog = SettingsDialog(self.window.root, self.window.theme, self)
        self._settings_dialog.show()

    def open_data_dir(self) -> None:
        open_in_explorer(DATA_DIR)

    def clear_history(self) -> None:
        if self.sampler is None:
            self._notify("演示模式：不写入历史")
            return
        dialog = ConfirmDialog(
            self.window.root, self.window.theme, "清空历史",
            "确定要清空全部历史运行记录吗？\n清空后基于历史的剩余时间估算会暂时失效。",
            ok_text="清空", danger=True)
        dialog.show()
        if dialog.wait():
            self.sampler.tracker.clear_history()
            self.config.save()
            self._notify("历史记录已清空")

    # ==================================================================
    # 实验操作
    # ==================================================================
    def _require_tracker(self):
        if self.sampler is None:
            self._notify("演示模式：仅用于预览界面")
            return None
        return self.sampler.tracker

    def _find_view(self, exp: ExperimentView) -> ExperimentView | None:
        if self._last_snapshot is None:
            return None
        for folder in self._last_snapshot.folders:
            for view in folder.experiments:
                if view.key == exp.key:
                    return view
        return None

    def open_folder_for(self, exp: ExperimentView) -> None:
        target = exp.folder
        if not target or target == "未知":
            if exp.log_path:
                target = os.path.dirname(exp.log_path)
            else:
                self._notify("该实验的文件夹未知")
                return
        target = target.strip().strip('"').rstrip("\\/")
        if not target:
            self._notify("该实验的文件夹未知")
            return
        if not os.path.isdir(target):
            self._notify("文件夹不存在：" + target)
            return
        opened = False
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            opened = True
        except Exception as exc:
            log_line(f"os.startfile failed: {exc}")
        if not opened:
            # 某些环境下 startfile 会被安全软件拦截，退回 explorer.exe
            try:
                import subprocess

                subprocess.Popen(["explorer.exe", os.path.normpath(target)],
                                 close_fds=True)
                opened = True
            except Exception as exc:
                log_line(f"explorer fallback failed: {exc}")
        if opened:
            self._notify("已打开文件夹")
        else:
            self._notify("打开文件夹失败：" + target)

    def show_context_menu(self, exp: ExperimentView, x_root: int, y_root: int) -> None:
        self.window.tooltip.hide()
        view = self._find_view(exp) or exp
        items: list[tuple] = [("header", view.name)]
        if view.idea:
            items.append(("header", f"想法：{view.idea}"))
        items.append(("item", "打开实验文件夹", lambda: self.open_folder_for(view)))
        items.append(("item", "编辑实验描述…", lambda: self.edit_description(view)))
        items.append(("item", "复制文件夹路径", lambda: self._copy(view.folder)))
        items.append(("item", "复制启动命令", lambda: self._copy(view.cmdline)))
        items.append(("sep", None))
        if view.paused:
            items.append(("item", "继续运行（从暂停处接着跑）",
                          lambda: self._resume_experiment(view)))
        else:
            items.append(("item", "安全暂停…（冻结进程，可继续）",
                          lambda: self.toggle_pause(view)))
        items.append(("item", "设置预计总时长…", lambda: self.set_manual_estimate(view)))
        if view.manual_total:
            items.append(("item", "清除手动预计时长", lambda: self.clear_manual_estimate(view)))
        if self.sampler is not None:
            items.append(("item", "指定进度日志…", lambda: self.pick_log_file(view)))
            if view.log_path:
                items.append(("item", "清除进度日志绑定",
                              lambda: self.clear_log_override(view)))
            items.append(("sep", None))
            items.append(("item", "结束该实验…", lambda: self.kill_experiment(view), "danger"))
        else:
            items.append(("header", "演示模式：暂停/编辑等操作仅真实实验可用"))
        self.window.menu.show(x_root, y_root, items)

    # -- 安全暂停 -------------------------------------------------------
    def _pause_warning(self, exp: ExperimentView) -> str:
        lines = [
            f"将冻结「{exp.name}」的整棵进程树（{len(exp.pids)} 个进程）：",
            exp.folder,
            "",
            "• 继续后从冻结点接着运行，不会从头重跑",
            "• 暂停期间仍占用显存、内存和已打开的文件",
            "• 请勿重启电脑或强制结束该进程，否则未保存进度会丢失",
            "• 网络/分布式训练暂停可能导致其它节点或 NCCL 超时",
        ]
        command = (exp.cmdline or "").lower()
        if any(token in command for token in _DISTRIBUTED_TOKENS):
            lines.append("")
            lines.append("⚠ 检测到分布式/多进程训练特征，建议先确认其它节点状态再暂停。")
        lines.append("")
        lines.append("是否安全暂停？")
        return "\n".join(lines)

    def toggle_pause(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        view = self._find_view(exp) or exp
        if view.paused:
            self._resume_experiment(view)
            return
        dialog = ConfirmDialog(
            self.window.root, self.window.theme, "安全暂停",
            self._pause_warning(view), ok_text="冻结实验", danger=False)
        dialog.show()
        if not dialog.wait():
            return
        result = tracker.pause(view.key)
        self._notify(result.get("message", "暂停操作已完成"))
        self._last_ui_ts = 0.0

    def _resume_experiment(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        result = tracker.resume(exp.key)
        self._notify(result.get("message", "继续操作已完成"))
        self._last_ui_ts = 0.0

    # -- 实验描述 -------------------------------------------------------
    def edit_description(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        view = self._find_view(exp) or exp
        auto_hint = ""
        if view.framework:
            auto_hint = f"{view.framework}（{view.description_source or '自动识别'}）"
        else:
            auto_hint = "未识别到框架，可手动填写"
        project_hint = ""
        try:
            meta = tracker.descriptions.folder_meta(view.folder)
            if meta.get("idea"):
                project_hint = f"项目文件建议想法：{meta['idea']}"
        except Exception:
            pass
        dialog = DescriptionDialog(
            self.window.root, self.window.theme, view,
            auto_hint=auto_hint, project_hint=project_hint)
        dialog.show()
        result = dialog.wait()
        if result == "clear":
            tracker.clear_description(view.fingerprint)
            self._notify("已清除实验描述")
        elif isinstance(result, dict):
            tracker.set_description(
                view.fingerprint, result.get("idea", ""),
                result.get("framework", ""), result.get("note", ""))
            self._notify("实验描述已保存")
        self._last_ui_ts = 0.0

    def set_manual_estimate(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        dialog = PromptDurationDialog(
            self.window.root, self.window.theme,
            "设置预计总时长",
            f"为「{exp.name}」设置一个总运行时长，剩余时间会按此推算。",
            initial_seconds=exp.manual_total,
            on_clear=lambda: None,
        )
        dialog.show()
        result = dialog.wait()
        if result == "clear":
            self.clear_manual_estimate(exp)
        elif isinstance(result, (int, float)) and result > 0:
            tracker.set_manual_estimate(exp.fingerprint, float(result))
            self._notify(f"已设定总时长 {fmt.fmt_elapsed_long(float(result))}")

    def clear_manual_estimate(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        tracker.set_manual_estimate(exp.fingerprint, None)
        self._notify("已清除手动预计时长")

    def pick_log_file(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        initial = os.path.dirname(exp.log_path) if exp.log_path else exp.folder
        if not initial or not os.path.isdir(initial):
            initial = os.path.expanduser("~")
        try:
            path = filedialog.askopenfilename(
                parent=self.window.root,
                title="选择该实验的进度日志文件",
                initialdir=initial,
                filetypes=[("日志文件", "*.log *.txt *.out *.stdout"), ("所有文件", "*.*")],
            )
        except Exception:
            path = ""
        if path:
            tracker.set_log_override(exp.fingerprint, path)
            self._notify("已绑定进度日志")

    def clear_log_override(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        tracker.set_log_override(exp.fingerprint, None)
        self._notify("已清除日志绑定，将重新自动发现")

    def kill_experiment(self, exp: ExperimentView) -> None:
        tracker = self._require_tracker()
        if tracker is None:
            return
        message = (
            f"将结束实验进程及其全部子进程：\n\n{exp.name}\n{exp.folder}\n\n"
            f"已运行 {fmt.fmt_elapsed_long(exp.elapsed)}，共 "
            f"{len(exp.pids)} 个进程。\n该操作不可撤销。"
        )
        dialog = ConfirmDialog(self.window.root, self.window.theme, "结束实验",
                               message, ok_text="结束实验", danger=True)
        dialog.show()
        if not dialog.wait():
            return
        killed = tracker.terminate(exp.key)
        self._notify(f"已结束 {len(killed)} 个进程" if killed else "未能结束进程")

    def _copy(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        try:
            self.window.root.clipboard_clear()
            self.window.root.clipboard_append(text)
            self._notify("已复制到剪贴板")
        except Exception:
            pass

    def _notify(self, text: str) -> None:
        try:
            self.window.flash_footer(text)
        except Exception:
            pass

    # ==================================================================
    # 退出
    # ==================================================================
    def quit_app(self, force: bool = False) -> None:
        if self._quitting or self._stopped:
            return
        tracker = self.sampler.tracker if self.sampler is not None else None
        paused = tracker.paused_sessions() if tracker is not None else []
        if paused and not self.demo and not force:
            message = (
                f"有 {len(paused)} 个实验处于暂停状态。\n\n"
                "退出后它们会继续保持冻结，下次打开本插件仍可点“继续”恢复；\n"
                "如果希望它们先恢复运行再退出，请先取消并点各自行末的 ▶。\n\n"
                "如果重启电脑或强制结束这些进程，未保存的进度会丢失。"
            )
            dialog = ConfirmDialog(
                self.window.root, self.window.theme, "退出时仍有暂停的实验",
                message, ok_text="仍然退出", danger=True)
            dialog.show()
            if not dialog.wait():
                return
        self._quitting = True
        self.shutdown()
        try:
            self.window.destroy()
        except Exception:
            pass

    def shutdown(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        log_line("shutting down")
        try:
            if self.sampler is not None:
                self.sampler.stop()
        except Exception:
            pass
        try:
            self.config.save()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _capture_window(window: MainWindow, path: str) -> bool:
    try:
        from PIL import ImageGrab

        root = window.root
        try:
            window.tooltip.hide()
            window.menu.close()
        except Exception:
            pass
        try:
            root.attributes("-alpha", 1.0)
        except Exception:
            pass
        root.update_idletasks()
        x = root.winfo_rootx()
        y = root.winfo_rooty()
        w = root.winfo_width()
        h = root.winfo_height()
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        image.save(path)
        return True
    except Exception as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="experiment-widget", add_help=True)
    parser.add_argument("--demo", action="store_true", help="演示模式（模拟数据）")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    parser.add_argument("--selftest", metavar="PNG", default=None,
                        help="启动后截图到指定 PNG 再退出（用于验收）")
    parser.add_argument("--duration", type=float, default=2.5,
                        help="selftest 截图前等待秒数")
    parser.add_argument("--no-single-instance", action="store_true",
                        help="允许同时运行多个实例（测试用）")
    args = parser.parse_args(argv)

    if os.environ.get("EXPERIMENT_WIDGET_DEBUG") or args.debug:
        os.environ["EXPERIMENT_WIDGET_DEBUG"] = "1"

    winproc.set_dpi_awareness()
    mutex = None
    if not args.selftest and not args.no_single_instance and not args.demo:
        mutex = winproc.acquire_single_instance(SINGLE_INSTANCE_NAME)
        if mutex is None:
            winproc.bring_window_to_front(APP_TITLE)
            return 0
    app = App(demo=args.demo)
    exit_code = 0
    if args.selftest:
        target = os.path.abspath(args.selftest)

        def capture_and_quit() -> None:
            ok = _capture_window(app.window, target)
            print(("screenshot: " if ok else "screenshot-failed: ") + target)
            app.quit_app(force=True)

        app.window.after(max(300, int(args.duration * 1000)), capture_and_quit)
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
    finally:
        if mutex:
            try:
                winproc._k32.CloseHandle(mutex)
            except Exception:
                pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
