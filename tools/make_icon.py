"""生成应用图标 assets/widget.ico（需要 Pillow，仅开发时使用）。"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")


def draw_icon(size: int) -> Image.Image:
    scale = 8
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 圆角底板
    radius = int(big * 0.24)
    draw.rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=radius,
        fill=(15, 22, 32, 255), outline=(44, 58, 77, 255),
        width=max(1, int(big * 0.012)),
    )
    # 内侧高光
    draw.rounded_rectangle(
        (int(big * 0.06), int(big * 0.06), int(big * 0.94), int(big * 0.94)),
        radius=int(radius * 0.82), outline=(30, 43, 59, 255),
        width=max(1, int(big * 0.008)),
    )

    # CPU 折线（蓝）
    points = [
        (0.16, 0.62), (0.30, 0.50), (0.42, 0.58), (0.56, 0.34),
        (0.68, 0.44), (0.82, 0.28),
    ]
    cpu = [(big * x, big * y) for x, y in points]
    draw.line(cpu, fill=(56, 189, 248, 255), width=int(big * 0.055), joint="curve")
    # GPU 折线（紫）
    gpu_points = [(0.16, 0.76), (0.32, 0.70), (0.48, 0.74), (0.64, 0.62), (0.82, 0.66)]
    gpu = [(big * x, big * y) for x, y in gpu_points]
    draw.line(gpu, fill=(167, 139, 250, 255), width=int(big * 0.045), joint="curve")

    # 状态点
    dot_r = big * 0.075
    cx, cy = big * 0.82, big * 0.28
    draw.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=(52, 211, 153, 255))
    ring = dot_r * 1.9
    draw.ellipse((cx - ring, cy - ring, cx + ring, cy + ring),
                 outline=(34, 110, 84, 255), width=max(1, int(big * 0.014)))

    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(ASSETS, exist_ok=True)
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    images = [draw_icon(size) for size in sizes]
    ico_path = os.path.join(ASSETS, "widget.ico")
    images[-1].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])
    images[-1].save(os.path.join(ASSETS, "widget.png"))
    print("saved", ico_path)

    # 用四种尺寸拼一张预览图，方便检查
    preview = Image.new("RGBA", (sum(sizes) + 10 * len(sizes), 256), (10, 15, 22, 255))
    x = 0
    for size in sizes:
        icon = draw_icon(size)
        preview.paste(icon, (x, (256 - size) // 2), icon)
        x += size + 10
    preview.save(os.path.join(ASSETS, "preview.png"))


if __name__ == "__main__":
    main()
