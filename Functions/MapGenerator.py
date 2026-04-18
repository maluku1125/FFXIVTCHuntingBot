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
    若 map_data 含 "viewport": [x1, y1, x2, y2]，則裁切並放大該區域。
    """
    img_path = os.path.join(_DATA_DIR, map_data["mapImage"].replace("Data/", ""))
    img = Image.open(img_path).convert("RGBA")
    w, h = img.size

    # ── Viewport 裁切 ──────────────────────────────────────────────────────────
    viewport = map_data.get("viewport")
    if viewport:
        vx1, vy1, vx2, vy2 = viewport
        # FFXIV 座標 → 像素
        crop_l = int((vx1 - 1) / _MAP_RANGE * w)
        crop_t = int((vy1 - 1) / _MAP_RANGE * h)
        crop_r = int((vx2 - 1) / _MAP_RANGE * w)
        crop_b = int((vy2 - 1) / _MAP_RANGE * h)
        img = img.crop((crop_l, crop_t, crop_r, crop_b)).resize((w, h), Image.LANCZOS)
        # 座標映射函式：依 viewport 範圍重映射
        vrange_x = vx2 - vx1
        vrange_y = vy2 - vy1
        def coord_to_px(cx, cy):
            return (
                int((cx - vx1) / vrange_x * w),
                int((cy - vy1) / vrange_y * h),
            )
    else:
        def coord_to_px(cx, cy):
            return (
                int((cx - 1) / _MAP_RANGE * w),
                int((cy - 1) / _MAP_RANGE * h),
            )

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    radius = max(16, int(w * 0.030))
    font_size = max(18, int(w * 0.038))
    font = _load_font(font_size)

    for pt in map_data["points"]:
        label = pt["label"]
        px, py = coord_to_px(pt["x"], pt["y"])

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


# 各伺服器顏色與縮寫
_SERVER_STYLES: list[tuple[str, tuple[int,int,int], str]] = [
    ("伊弗利特", (255, 140,  30), "火"),
    ("迦樓羅",   ( 30, 210, 210), "風"),
    ("利維坦",   ( 60, 100, 255), "水"),
    ("鳳凰",     (230,  60,  60), "鳳"),
    ("奧汀",     (160,  60, 220), "奧"),
    ("巴哈姆特", (220, 180,  30), "巴"),
    ("泰坦",     ( 50, 200,  80), "土"),
]


def generate_overview_map(map_data: dict, server_cleared: dict[str, set[str]]) -> discord.File:
    """
    多伺服器總覽地圖。

    map_data       : srank_data.json 中單一地圖的資料
    server_cleared : { 伺服器名稱: 已排除點位標籤集合 }
    每個點位依哪些伺服器尚未排除分別繪製彩色小圓點 + 縮寫。
    """
    img_path = os.path.join(_DATA_DIR, map_data["mapImage"].replace("Data/", ""))
    img = Image.open(img_path).convert("RGBA")
    w, h = img.size

    # ── Viewport 裁切（複用邏輯）────────────────────────────────────────────
    viewport = map_data.get("viewport")
    if viewport:
        vx1, vy1, vx2, vy2 = viewport
        crop_l = int((vx1 - 1) / _MAP_RANGE * w)
        crop_t = int((vy1 - 1) / _MAP_RANGE * h)
        crop_r = int((vx2 - 1) / _MAP_RANGE * w)
        crop_b = int((vy2 - 1) / _MAP_RANGE * h)
        img = img.crop((crop_l, crop_t, crop_r, crop_b)).resize((w, h), Image.LANCZOS)
        vrange_x = vx2 - vx1
        vrange_y = vy2 - vy1
        def coord_to_px(cx, cy):
            return (int((cx - vx1) / vrange_x * w), int((cy - vy1) / vrange_y * h))
    else:
        def coord_to_px(cx, cy):
            return (int((cx - 1) / _MAP_RANGE * w), int((cy - 1) / _MAP_RANGE * h))

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    dot_r    = max(10, int(w * 0.018))   # 小圓點半徑
    font_sz  = max(14, int(w * 0.026))   # 縮寫字型大小
    font     = _load_font(font_sz)

    import math

    for pt in map_data["points"]:
        label = pt["label"]
        cx, cy = coord_to_px(pt["x"], pt["y"])

        # 哪些伺服器這個點位尚未排除
        active = [
            (sname, color, abbr)
            for sname, color, abbr in _SERVER_STYLES
            if label not in server_cleared.get(sname, set())
        ]

        if not active:
            continue  # 所有伺服器都已排除此點位，不繪製

        n = len(active)
        if n == 1:
            # 只有一個伺服器 → 直接畫在中心
            positions = [(cx, cy)]
        else:
            # 多個伺服器 → 圍繞中心排列
            spread = dot_r * 1.8
            positions = [
                (
                    int(cx + spread * math.cos(2 * math.pi * i / n - math.pi / 2)),
                    int(cy + spread * math.sin(2 * math.pi * i / n - math.pi / 2)),
                )
                for i in range(n)
            ]

        for i, (sname, color, abbr) in enumerate(active):
            px, py = positions[i]
            fill   = (*color, 210)
            border = (max(0, color[0]-50), max(0, color[1]-50), max(0, color[2]-50), 255)

            draw.ellipse(
                [px - dot_r, py - dot_r, px + dot_r, py + dot_r],
                fill=fill,
                outline=border,
                width=2,
            )

            # 縮寫描邊 + 本體（textbbox 精確置中）
            bbox = draw.textbbox((0, 0), abbr, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            tx = px - tw // 2 - bbox[0]
            ty = py - th // 2 - bbox[1]
            for dx, dy in ((-1,-1),(1,-1),(-1,1),(1,1),(0,-1),(0,1),(-1,0),(1,0)):
                draw.text((tx+dx, ty+dy), abbr, font=font, fill=(0, 0, 0, 255))
            draw.text((tx, ty), abbr, font=font, fill=(255, 255, 255, 255))

    result = Image.alpha_composite(img, overlay).convert("RGB")
    buf = BytesIO()
    result.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="map.png")
