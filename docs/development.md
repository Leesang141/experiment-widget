# 开发、测试与截图

## 1. 环境

- Windows 10/11 64 位；
- Python 3.9+，需要 tkinter（官方 Python 安装包自带）；
- 运行时**不需要**任何第三方 Python 包；
- 开发截图工具 `tools/ui_screenshots.py` 需要 Pillow（仅开发时）；
- Windows API 全部通过 ctypes 调用，无编译依赖。

本机可使用 DSH 自带 Python：

```text
C:\Users\LeeSang123\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
```

`启动实验监控.vbs` 会自动查找可用的 `pythonw.exe`。

## 2. 运行

```powershell
# 无控制台（推荐）
双击 启动实验监控.vbs

# 带控制台调试
双击 调试启动.bat

# 直接运行
python run_widget.py

# 演示模式（不读取真实进程，适合看界面/截图）
python run_widget.py --demo
```

## 3. 测试

```powershell
# 单元测试
python -m unittest discover -s tests -v

# 真实 Windows / GPU 测试（会启动临时进程）
$env:WIDGET_LIVE_TESTS = "1"
python -m unittest tests.test_winproc tests.test_gpu -v

# 端到端：识别、子进程聚合、进度、历史估算
python tools/integration_check.py

# 真实进程暂停/继续 + 小组件重启后恢复暂停状态
python tools/pause_check.py

# 诊断当前每个实验的 ETA / 日志 / 未知原因
python tools/diagnose_eta.py
```

测试会启动临时合成进程，并在结束后清理；不会动用户正在跑的真实实验。

## 4. 截图

```powershell
# 生成 README 用的演示截图（preview/collapsed/menu/settings/pause/description）
python tools/ui_screenshots.py docs

# 单张自检截图
python run_widget.py --demo --selftest docs/preview.png --duration 10
```

`docs/preview_live.png` 是真实运行环境的截图，重新生成时建议用演示模式替换，避免泄露真实路径。

## 5. 代码结构

主程序在 `experiment_widget/`，模块职责见 [架构说明](architecture.md)。

- UI 线程只做绘制，不做进程/日志/GPU 采集；
- 采样线程通过 `Snapshot` 发布不可变视图模型；
- 配置/历史/描述/暂停文件都在 `data/`，首次运行自动创建；
- 所有窗口尺寸使用 design px，通过 `theme.px()` 适配 DPI。

## 6. 新增进度格式

1. 在 `progress._parse_line()` 增加解析分支，或在配置 `estimate.custom_progress_rules` 添加正则；
2. 在 `tests/test_progress.py` 增加样例；
3. 如果是“外层 epoch + 内层 batch”结构，确认 `extract_total_epochs` / `combine_epoch_progress` 能组合出整体进度；
4. 运行单元测试和 `tools/diagnose_eta.py` 验证真实日志。

## 7. 新增 UI 元素

- 主题色/字体在 `theme.py`；
- 自定义控件在 `widgets.py`；
- 卡片布局在 `cards.py`；
- 新交互在 `app.py` + `ui.py` 回调；
- 截图回归：`python run_widget.py --demo --selftest ...`。

## 8. 启动器

- `启动实验监控.vbs`：UTF-16LE + BOM，查找 pythonw 并无控制台启动；
- `调试启动.bat`：带控制台，打印 Python 路径与错误；
- `创建桌面快捷方式.vbs`：创建带图标的快捷方式。

修改 `.vbs` 时请保持 UTF-16LE + BOM，否则 Windows Script Host 可能无法解析。`.gitattributes` 已把 `.vbs` 标记为二进制，Git 不会改写编码。

## 9. 提交前检查

```powershell
git status
git diff --stat
# 确认 data/、日志、__pycache__、临时截图没有被加入
```

运行时数据可能包含本机路径、实验名称、历史记录，**不要提交**。
