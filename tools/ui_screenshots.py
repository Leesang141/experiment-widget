"""界面截图巡检：生成 README 用的预览图。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiment_widget import winproc  # noqa: E402

winproc.set_dpi_awareness()

from PIL import ImageGrab  # noqa: E402

from experiment_widget.app import App  # noqa: E402
from experiment_widget.dialogs import ConfirmDialog, DescriptionDialog  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs"


def grab_window(app, name, pad=0):
    root = app.window.root
    root.update_idletasks()
    root.attributes("-alpha", 1.0)
    x = root.winfo_rootx() - pad
    y = root.winfo_rooty() - pad
    w = root.winfo_width() + pad * 2
    h = root.winfo_height() + pad * 2
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(os.path.join(OUT, name))
    print("saved", name)


def grab_dialog(app, dialog, name):
    root = app.window.root
    win = dialog.win
    win.update_idletasks()
    x = min(root.winfo_rootx(), win.winfo_rootx()) - 26
    y = min(root.winfo_rooty(), win.winfo_rooty()) - 26
    x2 = max(root.winfo_rootx() + root.winfo_width(),
             win.winfo_rootx() + win.winfo_width()) + 26
    y2 = max(root.winfo_rooty() + root.winfo_height(),
             win.winfo_rooty() + win.winfo_height()) + 26
    ImageGrab.grab(bbox=(x, y, x2, y2)).save(os.path.join(OUT, name))
    print("saved", name)


def main() -> int:
    app = App(demo=True)
    steps = []

    def at(ms, fn):
        steps.append((ms, fn))

    def expanded():
        grab_window(app, "preview.png")

    def description():
        snapshot = app._last_snapshot
        view = snapshot.folders[0].experiments[0]
        dialog = DescriptionDialog(
            app.window.root, app.window.theme, view,
            auto_hint=f"{view.framework}（自动识别）",
            project_hint="项目文件建议想法：候选9 · 主对比实验")
        dialog.show()

        def cap():
            grab_dialog(app, dialog, "preview_description.png")
            dialog.close()

        app.window.after(650, cap)

    def pause_dialog():
        snapshot = app._last_snapshot
        view = snapshot.folders[0].experiments[0]
        dialog = ConfirmDialog(app.window.root, app.window.theme, "安全暂停",
                               app._pause_warning(view), ok_text="冻结实验")
        dialog.show()

        def cap():
            grab_dialog(app, dialog, "preview_pause.png")
            dialog.close()

        app.window.after(650, cap)

    def menu():
        snapshot = app._last_snapshot
        view = snapshot.folders[0].experiments[0]
        root = app.window.root
        app.show_context_menu(view, root.winfo_rootx() + 150,
                              root.winfo_rooty() + 300)

        def cap():
            grab_window(app, "preview_menu.png", pad=80)
            app.window.menu.close()

        app.window.after(500, cap)

    def collapse():
        app.window.toggle_collapsed_external()

        def cap():
            grab_window(app, "preview_collapsed.png", pad=20)

        app.window.after(500, cap)

    def expand_back():
        app.window.toggle_collapsed_external()

    def settings():
        app.open_settings()

        def cap():
            dialog = app._settings_dialog
            grab_dialog(app, dialog, "preview_settings.png")
            dialog.close()

        app.window.after(650, cap)

    def finish():
        app.quit_app(force=True)

    at(2200, expanded)
    at(2900, description)
    at(4400, pause_dialog)
    at(5900, menu)
    at(7400, collapse)
    at(8500, expand_back)
    at(9200, settings)
    at(10800, finish)
    for ms, fn in steps:
        app.window.after(ms, fn)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
