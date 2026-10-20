# 架构说明

## 总览

实验监控是一个 Windows 桌面小组件：Tkinter 负责界面，后台线程负责采集进程、GPU 和日志，主线程按固定间隔取最新快照刷新 UI。运行时**不联网**，不依赖第三方 Python 包（开发截图工具可选 Pillow）。

```text
┌──────────────────────────── UI 线程（Tk mainloop）────────────────────────────┐
│ App                                                                          │
│  ├─ MainWindow       标题栏 / 概览 / 滚动区 / 底栏 / 菜单 / 提示              │
│  ├─ FolderCard       按文件夹分组                                            │
│  ├─ ExperimentRow    单个实验：CPU/GPU/ETA/描述/暂停按钮                      │
│  └─ Dialogs          设置 / 安全暂停确认 / 编辑描述 / 输入时长                 │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    │ poll_latest() 取快照
┌───────────────────────────────────┴──────────────────────────────────────────┐
│ 后台采样线程（Sampler）                                                       │
│  ├─ winproc.snapshot_processes()   进程快照（Toolhelp + GetProcessTimes）      │
│  ├─ Tracker                        识别实验、聚合子进程、ETA、暂停状态         │
│  │   ├─ ProgressMiner              日志发现 + 进度解析 + 速率                 │
│  │   ├─ DescriptionStore           框架识别 / 实验描述                        │
│  │   ├─ PauseRegistry              暂停状态持久化                             │
│  │   └─ HistoryStore               历史运行时长                               │
│  └─ GpuReader                      nvidia-smi / PDH GPU 计数器                │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 模块职责

| 文件 | 职责 |
| --- | --- |
| `app.py` | 应用控制器：单实例、轮询快照、设置、右键菜单、打开文件夹、暂停/继续、编辑描述、退出确认、自检截图 |
| `ui.py` | 主窗口结构、折叠、滚动区、概览走势线、底栏、全局滚轮 |
| `cards.py` | 文件夹卡片与实验行：状态点、ETA、框架/想法、进度条、暂停/编辑按钮、tooltip |
| `widgets.py` | 自定义控件：滚动条、按钮、提示、右键菜单、进度条、圆角绘制 |
| `theme.py` | 深色主题、字体、DPI 缩放（design px → 实际像素） |
| `models.py` | `Snapshot / FolderView / ExperimentView / Estimate / ProgressView` 等不可变视图模型 |
| `winproc.py` | Windows API：进程/父子关系、命令行、PEB 工作目录、CPU 时间、内存、挂起/恢复/结束进程树、窗口相关 |
| `gpu.py` | NVIDIA `nvidia-smi` / `pmon` + Windows PDH GPU 引擎与显存计数器 |
| `progress.py` | 日志发现、进度解析、Epoch 组合、速率采样 |
| `estimate.py` | 实验识别、子进程聚合、CPU/GPU 归一化、ETA 选择、暂停状态、历史写入 |
| `descriptions.py` | 框架自动识别、项目文件想法读取、描述持久化 |
| `pauses.py` | `data/paused.json` 的读写、去重、清理 |
| `history.py` | `data/history.json` 的读写与中位数统计 |
| `sampler.py` | 后台采样线程和快照发布 |
| `demo.py` | 演示数据源（无真实进程） |
| `config.py` | 配置默认值、读写、数据目录、资源管理器打开 |
| `fmt.py` / `util.py` | 时间、百分比、字节格式化；日志与通用工具 |

## 数据流

1. Sampler 每 1 秒左右抓取一次进程快照和 GPU 数据；
2. Tracker 找出“实验根进程”（python / node / R / GPU 明显的自定义程序等），把它的子孙进程聚合成一个实验；
3. 对每个实验：
   - 读取工作目录、命令行、父进程命令行；
   - `ProgressMiner` 发现日志并解析进度，结合 `--epochs` 组合整体进度；
   - 计算 CPU 占整机百分比、GPU 利用率、显存、内存；
   - 按优先级选择 ETA：进度日志 → 手动总时长 → 历史中位数 → 启动以来平均速度；
   - 合并用户描述与暂停状态；
4. `Snapshot` 发布给 UI；UI 只负责按最新快照重绘，不阻塞采样。

## 运行时数据

| 路径 | 内容 | 是否应提交 Git |
| --- | --- | --- |
| `data/config.json` | 窗口位置、设置、手动总时长 | 否 |
| `data/history.json` | 历史运行时长 | 否 |
| `data/descriptions.json` | 实验描述 | 否（可能含本机路径） |
| `data/paused.json` | 暂停记录 | 否 |
| `data/widget.log` | 调试日志 | 否 |

`.gitignore` 已排除 `data/`、日志和缓存；上传 GitHub 时请保持这些文件不入库。

## 关键设计取舍

- **进程树识别**：以“根实验进程”为单位，避免 dataloader worker 被列成一堆实验；根进程结束后整组实验结束。
- **工作目录**：从 PEB 读取真实 cwd，而不是根据 exe 猜；读取失败才退回脚本目录/可执行文件目录。
- **GPU 数据**：NVIDIA 优先用 `pmon` 的进程 SM 利用率；其它显卡用 PDH `GPU Engine` 计数器，口径接近任务管理器。
- **暂停**：使用 Windows 线程挂起实现“进程级冻结”，保留内存/显存/文件句柄，不依赖脚本 checkpoint；因此不能跨重启恢复运行。
- **零运行时依赖**：所有 Windows API 通过 ctypes 调用，避免安装包和命令行窗口。

## 目录结构

```text
插件/
├─ run_widget.py                 # Python 入口
├─ 启动实验监控.vbs              # 无控制台启动
├─ 调试启动.bat                  # 带控制台调试
├─ 创建桌面快捷方式.vbs
├─ experiment_widget/             # 主程序包
├─ assets/widget.ico / widget.png
├─ docs/                         # 文档与截图
├─ tests/                        # 单元测试（含可选真实环境测试）
└─ tools/                        # 联调、暂停验证、截图、图标、演示实验
```
