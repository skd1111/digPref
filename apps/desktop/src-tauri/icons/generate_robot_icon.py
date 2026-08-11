"""生成 EAIDE 小机器人图标。

输出（按 Tauri 2 约定）：
    - icon.png        256x256 PNG（Linux / 通用）
    - 32x32.png        Windows 任务栏小尺寸
    - 128x128.png      通用
    - 128x128@2x.png   macOS retina
    - icon.ico         32x32 ICO（Windows 窗口/任务栏）

设计：圆润的方形机器人头 + 2 根天线 + 大圆眼（青色 #4ec9b0）+ 微笑嘴
配色贴合 EAIDE 深色主题（背景 #1e1e1e / 头部 #2d2d30 / 边框 #007acc）
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# EAIDE 主题色
BG = (30, 30, 30, 255)  # #1e1e1e  背景
HEAD = (45, 45, 48, 255)  # #2d2d30  机器人头
BORDER = (0, 122, 204, 255)  # #007acc  边框（VSCode 蓝）
EYE = (78, 201, 176, 255)  # #4ec9b0  眼睛（dev 绿）
MOUTH = (78, 201, 176, 255)  # 同眼睛色
ANTENNA = (180, 180, 180, 255)
HIGHLIGHT = (90, 90, 95, 255)  # 头部高光


def draw_robot(size: int) -> Image.Image:
    """绘制 size×size 的小机器人（带透明背景圆角矩形）"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    s = size
    pad = max(2, s // 16)

    # 圆角背景（深色填充 + 蓝色边框）
    radius = s // 6
    bg_box = [pad, pad, s - pad, s - pad]
    d.rounded_rectangle(bg_box, radius=radius, fill=BG, outline=BORDER, width=max(1, s // 32))

    # 头部（圆角矩形，比背景稍小）
    head_pad = s // 5
    head_box = [head_pad, head_pad + s // 12, s - head_pad, s - head_pad]
    d.rounded_rectangle(
        head_box,
        radius=s // 8,
        fill=HEAD,
        outline=HIGHLIGHT,
        width=max(1, s // 64),
    )

    # 头部高光（顶部细线）
    hl_y = head_pad + s // 10
    d.line(
        [(head_pad + s // 10, hl_y), (s - head_pad - s // 10, hl_y)],
        fill=HIGHLIGHT,
        width=max(1, s // 64),
    )

    # 眼睛（2 个大圆，青色）
    eye_r = s // 8
    eye_y = head_pad + s // 4
    eye_left_x = s // 2 - s // 6
    eye_right_x = s // 2 + s // 6
    d.ellipse(
        [eye_left_x - eye_r, eye_y - eye_r, eye_left_x + eye_r, eye_y + eye_r],
        fill=EYE,
    )
    d.ellipse(
        [eye_right_x - eye_r, eye_y - eye_r, eye_right_x + eye_r, eye_y + eye_r],
        fill=EYE,
    )
    # 眼睛高光（小白点）
    hl_r = max(1, s // 32)
    d.ellipse(
        [
            eye_left_x - eye_r // 2,
            eye_y - eye_r // 2,
            eye_left_x - eye_r // 2 + hl_r,
            eye_y - eye_r // 2 + hl_r,
        ],
        fill=(255, 255, 255, 255),
    )
    d.ellipse(
        [
            eye_right_x - eye_r // 2,
            eye_y - eye_r // 2,
            eye_right_x - eye_r // 2 + hl_r,
            eye_y - eye_r // 2 + hl_r,
        ],
        fill=(255, 255, 255, 255),
    )

    # 嘴（微笑弧线，用 arc）
    mouth_box = [s // 3, head_pad + s // 2, 2 * s // 3, head_pad + s // 2 + s // 4]
    d.arc(mouth_box, start=10, end=170, fill=MOUTH, width=max(1, s // 32))

    # 天线（2 根线 + 顶端小圆）
    ant_w = max(1, s // 64)
    ant_y_top = pad + s // 20
    ant_y_bot = head_pad - s // 40
    # 左天线
    d.line([(s // 3, ant_y_bot), (s // 3 - s // 16, ant_y_top)], fill=ANTENNA, width=ant_w)
    d.ellipse(
        [
            s // 3 - s // 16 - s // 40,
            ant_y_top - s // 40,
            s // 3 - s // 16 + s // 40,
            ant_y_top + s // 40,
        ],
        fill=ANTENNA,
    )
    # 右天线
    d.line([(2 * s // 3, ant_y_bot), (2 * s // 3 + s // 16, ant_y_top)], fill=ANTENNA, width=ant_w)
    d.ellipse(
        [
            2 * s // 3 + s // 16 - s // 40,
            ant_y_top - s // 40,
            2 * s // 3 + s // 16 + s // 40,
            ant_y_top + s // 40,
        ],
        fill=ANTENNA,
    )

    return img


def save_ico(img: Image.Image, path: Path) -> None:
    """用 PIL 保存 32x32 ICO（合法 Windows ICO 格式）"""
    img.save(path, format="ICO", sizes=[(32, 32), (16, 16)])
    print(f"  → {path} ({path.stat().st_size} 字节)")


# 256x256 PNG（主图）
img_256 = draw_robot(256)
img_256.save(OUT_DIR / "icon.png", format="PNG")
print(f"  → {OUT_DIR / 'icon.png'}")

# 128x128 + 128@2x (256x256)
img_128 = draw_robot(128)
img_128.save(OUT_DIR / "128x128.png", format="PNG")
print(f"  → {OUT_DIR / '128x128.png'}")

# 128@2x.png 实际是 256x256
img_256.save(OUT_DIR / "128x128@2x.png", format="PNG")
print(f"  → {OUT_DIR / '128x128@2x.png'}")

# 32x32
img_32 = draw_robot(32)
img_32.save(OUT_DIR / "32x32.png", format="PNG")
print(f"  → {OUT_DIR / '32x32.png'}")

# ICO（PIL 自动生成多尺寸）
img_256.save(OUT_DIR / "icon.ico", format="ICO", sizes=[(256, 256), (48, 48), (32, 32), (16, 16)])
print(f"  → {OUT_DIR / 'icon.ico'}")

print(f"\n[OK] All icons generated to {OUT_DIR}")
