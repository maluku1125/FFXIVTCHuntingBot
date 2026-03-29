from __future__ import annotations

import os
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont

# FFXIV 地圖座標系統：1~41 對應圖片 0%~100%
_MAP_RANGE = 41.0

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "Data"))

# 嘗試載入字型（無法載入則使用預設）
def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msjh.ttc",    # 微軟正黑體（繁中）
        "C:/Windows/Fonts/msyh.ttc",    # 微軟雅黑
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def generate_map(map_data: dict, cleared: set[str]) -> discord.File:
    """
    在地圖圖片上繪製點位標記，回傳 discord.File。

    map_data  : srank_data.json 中單一地圖的資料
    cleared   : 已排除的點位標籤集合，如 {'A', 'C'}
    """
    img_path = os.path.join(_DATA_DIR, map_data["mapImage"].replace("Data/", ""))
    img = Image.open(img_path).convert("RGBA")
    w, h = img.size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    radius = max(16, int(w * 0.030))
    font_size = max(18, int(w * 0.038))
    font = _load_font(font_size)

    for pt in map_data["points"]:
        label = pt["label"]
        # 座標轉換：FFXIV (1~41) → 圖片像素
        px = int((pt["x"] - 1) / _MAP_RANGE * w)
        py = int((pt["y"] - 1) / _MAP_RANGE * h)

        is_cleared = label in cleared

        # 圓圈：已排除=紅，未排除=綠
        fill_color   = (220, 50,  50,  200) if is_cleared else (50,  200, 80,  200)
        border_color = (180, 20,  20,  255) if is_cleared else (20,  160, 50,  255)

        draw.ellipse(
            [px - radius, py - radius, px + radius, py + radius],
            fill=fill_color,
            outline=border_color,
            width=3,
        )

        # 標籤文字（亮黃色，帶黑色描邊）
        text_x = px - font_size // 3
        text_y = py - font_size // 2
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2), (0, -2), (0, 2), (-2, 0), (2, 0)):
            draw.text((text_x + dx, text_y + dy), label, font=font, fill=(0, 0, 0, 255))
        draw.text((text_x, text_y), label, font=font, fill=(0, 0, 0, 255))

    result = Image.alpha_composite(img, overlay).convert("RGB")

    buf = BytesIO()
    result.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="map.png")
