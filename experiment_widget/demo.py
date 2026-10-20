"""演示数据源：不读取真实进程，用于预览界面 / 截图 / 无实验时演示。"""

from __future__ import annotations

import math
import time
from typing import Optional

from .estimate import folder_display_name
from .models import (
    Estimate,
    ExperimentView,
    FolderView,
    GpuDevice,
    ProgressView,
    Snapshot,
    SystemView,
)


class DemoSource:
    """生成一份随时间轻微变化的模拟快照，展示进度 / 历史 / 暂停 / 描述等状态。"""

    def __init__(self) -> None:
        self.t0 = time.time()
        self._specs = [
            {
                "folder": r"D:\CVPR\实验",
                "title": "run_h2h3.py",
                "cmdline": r'C:\Users\LeeSang123\miniconda3\envs\torch\python.exe -I -u run_h2h3.py --methods balm,pmr --features D:/CVPR/实验/results/h2_cremad/features/cremad_features.npz --n_classes 6',
                "age": 5280.0,
                "cpu": 17.0,
                "gpu": 29.0,
                "vram": 173.0,
                "eta0": 780.0,
                "kind": "progress",
                "progress": ("seed45_fold7", 46, 50),
                "source": "工作目录",
                "gpu_source": "pmon",
                "phase": 0.4,
                "framework": "PyTorch",
                "idea": "候选9 · 主对比实验",
                "note": "CREMA-D 五分类，balm + pmr 融合",
            },
            {
                "folder": r"D:\CVPR\实验",
                "title": "train_predict.py",
                "cmdline": r'C:\Users\LeeSang123\miniconda3\envs\torch\python.exe -I train_predict.py --methods naive_concat,balm,pmr --S 50 --device cpu',
                "age": 610.0,
                "cpu": 43.0,
                "gpu": 0.0,
                "vram": 0.0,
                "eta0": 1980.0,
                "kind": "history",
                "history_n": 4,
                "history_median": 2590.0,
                "source": "工作目录",
                "gpu_source": "engine",
                "phase": 2.2,
                "framework": "PyTorch",
                "idea": "候选10 · 补 8 下界实验",
                "note": "CPU 训练，等待对比审查结果",
                "paused": True,
            },
            {
                "folder": r"D:\Projects\多模态实验",
                "title": "extract_vision.py",
                "cmdline": r'C:\Users\LeeSang123\miniconda3\envs\torch\python.exe -I 实验/scripts/v2/extract_face_clip_vision.py',
                "age": 765.0,
                "cpu": 6.0,
                "gpu": 14.0,
                "vram": 991.0,
                "eta0": 4380.0,
                "kind": "progress",
                "progress": ("500/7442", 500, 7442),
                "source": "工作目录",
                "gpu_source": "pmon",
                "phase": 4.1,
                "framework": "PyTorch + Transformers",
                "idea": "CLIP 视觉特征抽取",
                "note": "给下游 probe 准备特征",
            },
            {
                "folder": r"D:\bench\llm-eval",
                "title": "eval.py",
                "cmdline": r'python eval.py --model qwen2.5-7b --bench mmlu,gsm8k --limit 500',
                "age": 143.0,
                "cpu": 88.0,
                "gpu": 0.0,
                "vram": 0.0,
                "eta0": None,
                "kind": "unknown",
                "source": "脚本目录",
                "gpu_source": "engine",
                "phase": 1.3,
                "framework": "vLLM",
                "idea": "Qwen2.5 MMLU / GSM8K 基线",
                "note": "",
            },
            {
                "folder": r"D:\bench\llm-eval",
                "title": "sweep_temperature.py",
                "cmdline": r'python sweep_temperature.py --models qwen2.5-7b --temps 0.0,0.3,0.7',
                "age": 340.0,
                "cpu": 12.0,
                "gpu": 0.0,
                "vram": 0.0,
                "eta0": 1400.0,
                "kind": "progress",
                "progress": ("3/10", 3, 10),
                "source": "脚本目录",
                "gpu_source": "engine",
                "phase": 3.0,
                "framework": "HuggingFace",
                "idea": "温度参数消融",
                "note": "",
            },
            {
                "folder": r"D:\CVPR\消融",
                "title": "ablation_lr.py",
                "cmdline": r'C:\Users\LeeSang123\miniconda3\envs\torch\python.exe ablation_lr.py --lrs 1e-4,3e-4,1e-3',
                "age": 95.0,
                "cpu": 22.0,
                "gpu": 11.0,
                "vram": 412.0,
                "eta0": 2600.0,
                "kind": "unknown",
                "source": "工作目录",
                "gpu_source": "pmon",
                "phase": 5.2,
                "framework": "PyTorch",
                "idea": "学习率鲁棒性检查",
                "note": "",
            },
        ]

    # -- 外部接口 -------------------------------------------------------
    def poll_latest(self) -> Optional[Snapshot]:
        return self._snapshot()

    def stop(self) -> None:
        return None

    # -- 数据生成 -------------------------------------------------------
    def _snapshot(self) -> Snapshot:
        now = time.time()
        elapsed_total = now - self.t0
        groups: dict[str, FolderView] = {}
        total = 0
        paused_total = 0
        for index, spec in enumerate(self._specs):
            paused = bool(spec.get("paused"))
            age = spec["age"] + (0.0 if paused else elapsed_total)
            phase = spec["phase"]
            cpu = 0.0 if paused else max(
                0.0, spec["cpu"] + 4.0 * math.sin(elapsed_total / 6.0 + phase))
            gpu = 0.0 if paused else max(
                0.0, spec["gpu"] + 5.0 * math.sin(elapsed_total / 5.0 + phase * 1.7))
            if spec["gpu"] <= 0.01:
                gpu = 0.0
            progress = None
            estimate: Optional[Estimate]
            kind = spec["kind"]
            if paused:
                history_median = spec.get("history_median") or 0.0
                estimate = Estimate(
                    seconds=spec["eta0"],
                    basis="history",
                    confidence="high",
                    detail="暂停前的剩余估算（已冻结）",
                    total_seconds=history_median,
                    progress_pct=(age / history_median * 100.0) if history_median else None,
                    basis_label="历史",
                )
            elif kind == "progress":
                label, cur0, total_units = spec["progress"]
                eta0 = spec["eta0"]
                rate = max(1e-6, (total_units - cur0) / eta0)
                cur = min(total_units - 1, cur0 + rate * elapsed_total)
                pct = 100.0 * cur / total_units
                remaining = max(0.0, (total_units - cur) / rate)
                progress = ProgressView(
                    pct=pct,
                    label=f"{int(cur)}/{total_units}",
                    rate=rate,
                    eta_seconds=remaining,
                    source="日志 · 计数",
                )
                estimate = Estimate(
                    seconds=remaining,
                    basis="progress",
                    confidence="high",
                    detail=f"日志 · 计数 {int(cur)}/{total_units}",
                    total_seconds=age + remaining,
                    progress_pct=pct,
                    basis_label="进度",
                )
            elif kind == "history":
                remaining = max(0.0, spec["eta0"] - elapsed_total)
                estimate = Estimate(
                    seconds=remaining,
                    basis="history",
                    confidence="high",
                    detail=f"历史中位时长 {int(spec['history_median'])} 秒（{spec['history_n']} 次）",
                    total_seconds=spec["history_median"],
                    progress_pct=min(100.0, age / spec["history_median"] * 100.0),
                    basis_label="历史",
                )
            else:
                estimate = Estimate(
                    seconds=None,
                    basis="none",
                    confidence="none",
                    detail="暂无依据：右键可设定预计总时长；有进度日志或历史运行记录后会自动估算",
                    basis_label="未知",
                )
            view = ExperimentView(
                key=f"demo-{index}-{spec['title']}",
                fingerprint=f"demo|{spec['title']}",
                name=spec["title"],
                cmdline=spec["cmdline"],
                folder=spec["folder"],
                folder_source=spec["source"],
                exe=spec["cmdline"].split('"')[1] if '"' in spec["cmdline"] else "python.exe",
                pids=[1000 + index * 17 + 7],
                started_at=now - age,
                elapsed=age,
                cpu_pct=cpu,
                gpu_util=gpu if spec["gpu"] > 0 else 0.0,
                gpu_mem_mb=spec["vram"],
                mem_mb=300.0 + 120.0 * math.sin(phase),
                gpu_source=spec["gpu_source"],
                idle_seconds=120.0 if paused else 0.0,
                estimate=estimate,
                progress=progress,
                log_path=r"D:\CVPR\实验\logs\06\cremad_balm_pmr_rerun2.log"
                if kind == "progress" and not paused
                else None,
                manual_total=None,
                framework=spec.get("framework", ""),
                idea=spec.get("idea", ""),
                note=spec.get("note", ""),
                description_source="演示",
                paused=paused,
                paused_seconds=elapsed_total if paused else 0.0,
            )
            if paused:
                paused_total += 1
            total += 1
            group = groups.get(spec["folder"])
            if group is None:
                group = FolderView(
                    folder=spec["folder"],
                    display=folder_display_name(spec["folder"]),
                )
                groups[spec["folder"]] = group
            group.experiments.append(view)
        for group in groups.values():
            group.count = len(group.experiments)
            group.cpu_pct = min(100.0, sum(exp.cpu_pct for exp in group.experiments))
            utils = [exp.gpu_util for exp in group.experiments if exp.gpu_util]
            group.gpu_util = sum(utils) if utils else 0.0
            mems = [exp.gpu_mem_mb for exp in group.experiments if exp.gpu_mem_mb]
            group.gpu_mem_mb = sum(mems) if mems else 0.0
        device = GpuDevice(
            index=0,
            name="NVIDIA GeForce RTX 4060 Laptop GPU",
            util_pct=38.0 + 6.0 * math.sin(elapsed_total / 7.0),
            mem_used_mb=3150.0,
            mem_total_mb=8188.0,
            temp_c=54.0,
        )
        system = SystemView(
            cpu_pct=52.0 + 7.0 * math.sin(elapsed_total / 5.0),
            gpu_util=device.util_pct,
            gpu_count=1,
            vram_used_mb=device.mem_used_mb,
            vram_total_mb=device.mem_total_mb,
            ram_used_mb=19200.0,
            ram_total_mb=32472.0,
        )
        return Snapshot(
            ts=now,
            system=system,
            folders=list(groups.values()),
            total_experiments=total,
            hidden_experiments=0,
            paused_experiments=paused_total,
            history_count=12,
            demo=True,
        )
