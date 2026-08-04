"""生成一张最小可用的 16x16 32 位 ICO（格式合法）。

合法的 ICO 由以下几段拼成：
    ICONDIR       （6 字节）
    ICONDIRENTRY  （16 字节）
    BITMAPINFOHEADER（40 字节）
    像素数据：BGRA，从下往上，height*width*4 字节
    AND 掩码：每像素 1 位，行宽补齐到 32 位
"""
import struct
import sys
from pathlib import Path

w, h = 16, 16

# BITMAPINFOHEADER
info = struct.pack(
    "<IiiHHIIiiII",
    40,                # biSize
    w,                 # biWidth
    h * 2,             # biHeight（×2 是因为还要算 AND 掩码）
    1,                 # biPlanes
    32,                # biBitCount
    0,                 # biCompression（BI_RGB）
    w * h * 4 + (w * h) // 8,  # biSizeImage（粗略估算）
    0, 0, 0, 0,
)

# 像素数据：BGRA，统一填深蓝
pixel_size = w * h * 4
pixels = b""
for _ in range(w * h):
    pixels += bytes([0x40, 0x60, 0xA0, 0xFF])  # B, G, R, A

# AND 掩码：每像素 1 位，全部 0（不透明），行宽补齐到 32 位
row_bytes = (w + 31) // 32 * 4
and_mask = b"\x00" * (row_bytes * h)

# ICONDIR（6 字节）
icondir = struct.pack("<HHH", 0, 1, 1)  # reserved, type=1（ICO）, count=1

# ICONDIRENTRY（16 字节）
img_size = len(info) + len(pixels) + len(and_mask)
entry = struct.pack(
    "<BBBBHHII",
    w if w < 256 else 0,
    h if h < 256 else 0,
    0,                 # palette
    0,                 # reserved
    1,                 # color planes
    32,                # bits per pixel
    img_size,          # image size
    6 + 16,            # offset（header + entry）
)

data = icondir + entry + info + pixels + and_mask

out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "icon.ico").write_bytes(data)
print(f"已写入 {(out_dir / 'icon.ico')}（{len(data)} 字节）")
