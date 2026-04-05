"""
SRankSpecial.py — 特殊 S 怪生成條件計時器
11 隻 S 怪硬碼條件，每 5 分鐘刷新一次 embed，顯示下 3 個可出現窗口。

條件算法：
    WEATHER_DURATION = 1400s / 天氣時段
    ET 日 = 4200s；ET 月 = 32 ET 日；ET 年 = 12 月
    月相：每 4 ET 日換相，共 8 相
        新月 = day 1-4（0-indexed day% 32 在 0-3）
        滿月 = day 13-16 相位索引 4，即 day 13-16（1-indexed: 13~16）
        但依遊戲實測，滿月視窗 = ET月 day 16-20（用戶指定）
"""
from __future__ import annotations

import json
import os
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ── 路徑 ──────────────────────────────────────────────────────────────────────
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
_FUNC_DIR  = os.path.normpath(os.path.join(_THIS_DIR, ".."))
_ROOT_DIR  = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", ".."))
_DATA_FILE = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "Data", "weather_data.json"))
_STATE_FILE = os.path.normpath(os.path.join(_ROOT_DIR, "Config", "srank_special_state.json"))
_CONFIG_PATH = os.path.normpath(os.path.join(_ROOT_DIR, "Config", "FFXIVTC-Huntingbot_config.ini"))

if _FUNC_DIR not in sys.path:
    sys.path.insert(0, _FUNC_DIR)
from BasicFunction import is_allowed  # noqa: E402

import configparser

# ── 天氣資料 ──────────────────────────────────────────────────────────────────
with open(_DATA_FILE, "r", encoding="utf-8") as _f:
    _ZONE_DATA: dict = json.load(_f)["zones"]

# ── ET / 天氣常數 ─────────────────────────────────────────────────────────────
_WD          = 1400    # 天氣時段長度（現實秒）
_ET_DAY      = 4200    # 現實秒 / ET 日
_ET_MONTH    = 32      # ET 日 / 月
_ET_YEAR_M   = 12      # 月 / 年
_ET_YEAR_S   = _ET_DAY * _ET_MONTH * _ET_YEAR_M  # 現實秒 / ET 年

WEATHER_EMOJI: dict[str, str] = {
    "碧空": "☀️", "晴朗": "🌤️", "陰雲": "⛅", "薄霧": "🌫️",
    "小雨": "🌧️", "暴雨": "🌦️", "雷雨": "⛈️", "打雷": "🌩️",
    "小雪": "❄️", "暴雪": "🌨️", "強風": "💨", "微風": "🍃",
    "靈風": "🌀",
}


# ── 算法核心（移植自 Asvel/ffxiv-weather, MIT）────────────────────────────────
def _forecast_target(unix: int) -> int:
    bell       = unix // 175
    increment  = (bell + 8 - (bell % 8)) % 24
    total_days = (unix // _ET_DAY) & 0xFFFFFFFF
    calc_base  = total_days * 100 + increment
    step1      = ((calc_base << 11) ^ calc_base) & 0xFFFFFFFF
    step2      = ((step1 >> 8) ^ step1) & 0xFFFFFFFF
    return step2 % 100


def _weather_at(zone_key: str, unix: int) -> str:
    """取得某地圖在指定 unix 秒的天氣名稱。"""
    target = _forecast_target(unix)
    for threshold, weather in _ZONE_DATA[zone_key]["rates"]:
        if target < threshold:
            return weather
    return _ZONE_DATA[zone_key]["rates"][-1][1]


def _window_start(unix: int) -> int:
    """將 unix 秒對齊到所在天氣時段的開始。"""
    return (unix // _WD) * _WD


# ── ET 工具 ───────────────────────────────────────────────────────────────────
def _et_day_unix(et_year: int, et_month: int, et_day: int, et_hour: int = 0) -> int:
    """ET 年月日時 → 現實 Unix 秒（ET hour 0-23 → 0-4199）。"""
    day_of_era = (
        (et_year - 1) * _ET_MONTH * _ET_YEAR_M
        + (et_month - 1) * _ET_MONTH
        + (et_day - 1)
    )
    return day_of_era * _ET_DAY + et_hour * (_ET_DAY // 24)


def _parse_et_now(ts: float | None = None) -> dict:
    """當前（或指定 unix）的 ET 分解：year / month / day / hour。
    t 為 ET 秒，需用 ET 秒常數作除數（與 EorzeaTime._parse_et 一致）。
      ET 秒 / ET 年  = 86400 × 32 × 12 = 33,177,600
      ET 秒 / ET 月  = 86400 × 32      =  2,764,800
      ET 秒 / ET 日  = 86400
      ET 秒 / ET 小時 = 3600
    """
    t = int((ts if ts is not None else time.time()) * (144 / 7))
    return {
        "year":  t // 33177600,
        "month": (t // 2764800) % 12 + 1,
        "day":   (t // 86400)   % 32 + 1,
        "hour":  (t // 3600)    % 24,
    }


def _adj_et_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = (year - 1) * _ET_YEAR_M + (month - 1) + delta
    return total // _ET_YEAR_M + 1, total % _ET_YEAR_M + 1


# ── 窗口搜尋函式 ──────────────────────────────────────────────────────────────

def _next_et_windows(
    h_start: int, h_end: int, n: int = 3
) -> list[tuple[int, int]]:
    """
    掃描未來 ET 時段，找出符合 [h_start, h_end) 的現實時間窗口。
    支援跨午夜：h_end < h_start（如 17→3 表示 17:00 到次日 3:00）。
    回傳 [(real_start_unix, real_end_unix), ...]
    """
    now    = int(time.time())
    et_now = _parse_et_now(now)
    result: list[tuple[int, int]] = []

    # 從目前 ET 年開始掃，最多掃 600 個 ET 日（約 50 個 ET 月）
    wrap    = h_end <= h_start  # 跨午夜
    year    = et_now["year"]
    month   = et_now["month"]
    day_idx = et_now["day"]    # 1-indexed

    for _ in range(600):
        day_base = _et_day_unix(year, month, day_idx)

        if wrap:
            # 窗口為 h_start → h_end（次日）
            real_s = day_base + h_start * (_ET_DAY // 24)
            real_e = day_base + _ET_DAY + h_end * (_ET_DAY // 24)
        else:
            real_s = day_base + h_start * (_ET_DAY // 24)
            real_e = day_base + h_end   * (_ET_DAY // 24)

        # 排除已完全過去的窗口
        if real_e > now:
            real_s = max(real_s, now)
            result.append((real_s, real_e))
            if len(result) >= n:
                break

        # 前進一 ET 日
        day_idx += 1
        if day_idx > _ET_MONTH:
            day_idx = 1
            year, month = _adj_et_month(year, month, 1)

    return result


def _next_moonphase_et_windows(
    day_s: int,
    day_e: int,
    h_s: int,
    h_e: int,
    first_day_h_s: int | None = None,
    n: int = 3,
) -> list[tuple[int, int]]:
    """
    掃描每個 ET 月，過濾月相日（day_s ~ day_e），返回 ET 時段窗口。
    first_day_h_s: 首日不同的起始 ET 小時（None 表示與其他日相同）。
    h_e < h_s 表示跨午夜。
    回傳 [(real_start, real_end), ...]，每個代表一個每日窗口。
    """
    now     = int(time.time())
    et_now  = _parse_et_now(now)
    result: list[tuple[int, int]] = []

    year  = et_now["year"]
    month = et_now["month"]
    wrap  = h_e <= h_s  # 跨午夜

    for _ in range(30):  # 最多掃 30 個 ET 月
        for day in range(day_s, day_e + 1):
            h_start_day = (first_day_h_s if (day == day_s and first_day_h_s is not None) else h_s)
            day_base = _et_day_unix(year, month, day)

            if wrap:
                real_s = day_base + h_start_day * (_ET_DAY // 24)
                real_e = day_base + _ET_DAY + h_e * (_ET_DAY // 24)
            else:
                real_s = day_base + h_start_day * (_ET_DAY // 24)
                real_e = day_base + h_e * (_ET_DAY // 24)

            if real_e <= now:
                continue

            real_s = max(real_s, now)
            result.append((real_s, real_e))
            if len(result) >= n:
                return result

        year, month = _adj_et_month(year, month, 1)

    return result


def _next_moonphase_continuous_window(
    day_s: int,
    day_e: int,
    first_day_h: int,
    n: int = 3,
) -> list[tuple[int, int]]:
    """
    整段連續窗口：ET 月 day_s first_day_h:00 → day_(e+1) 00:00
    用於 i. 巨大鰩：day16 12:00 → day21 00:00
    回傳 [(real_start, real_end), ...]
    """
    now    = int(time.time())
    et_now = _parse_et_now(now)
    result: list[tuple[int, int]] = []

    year  = et_now["year"]
    month = et_now["month"]

    for _ in range(30):
        real_s = _et_day_unix(year, month, day_s, first_day_h)
        real_e = _et_day_unix(year, month, day_e + 1, 0)

        if real_e > now:
            real_s = max(real_s, now)
            result.append((real_s, real_e))
            if len(result) >= n:
                break

        year, month = _adj_et_month(year, month, 1)

    return result


def _continuous_weather_windows(
    zone: str,
    weathers: set[str],
    min_real_min: int,
    n: int = 3,
) -> list[tuple[int, int]]:
    """
    找到「zone 連續 min_real_min 分鐘天氣在 weathers 內」的觸發時間點。
    回傳 [(trigger_unix, rain_end_unix), ...]
    trigger = 連續段開始後 min_real_min 分鐘（條件成立時刻）；rain_end = 該段雨結束時刻。
    掃描範圍：未來 30 天現實時間。
    """
    now      = int(time.time())
    min_secs = min_real_min * 60
    scan_end = now + 30 * 86400

    result: list[tuple[int, int]] = []
    t = _window_start(now)
    streak_start: int | None = None

    while t < scan_end:
        w = _weather_at(zone, t)
        if w in weathers:
            if streak_start is None:
                streak_start = t
            # 用「本窗口結束」判斷，2 個連續窗口（2800s）即可觸發 30min 條件
            if t + _WD - streak_start >= min_secs:
                trigger_time = streak_start + min_secs
                # 掃到這段雨的結尾
                tt = t + _WD
                while tt < scan_end and _weather_at(zone, tt) in weathers:
                    tt += _WD
                rain_end = tt
                if rain_end > now:
                    result.append((trigger_time, rain_end))
                    if len(result) >= n:
                        break
                t = tt          # 跳過這段雨，避免重複記錄
                streak_start = None
                continue
        else:
            streak_start = None

        t += _WD

    return result


def _continuous_no_weather_windows(
    zone: str,
    rain_weathers: set[str],
    min_real_min: int,
    n: int = 3,
) -> list[tuple[int, int]]:
    """
    找到「連續 min_real_min 分鐘不是 rain_weathers」的觸發時間點。
    回傳 [(trigger_unix, dry_end_unix), ...]
    trigger = 乾燥段開始後 min_real_min 分鐘（條件成立時刻）；dry_end = 下次降雨開始。
    掃描範圍：未來 30 天。
    """
    now      = int(time.time())
    min_secs = min_real_min * 60
    scan_end = now + 30 * 86400

    result: list[tuple[int, int]] = []
    t = _window_start(now)
    dry_start: int | None = None

    while t < scan_end:
        w = _weather_at(zone, t)
        if w not in rain_weathers:
            if dry_start is None:
                dry_start = t
            # 本窗口結束時已達條件（同 _continuous_weather_windows 的判斷方式）
            if t + _WD - dry_start >= min_secs:
                trigger = dry_start + min_secs
                # 掃到這段乾燥的結尾（下次降雨）
                tt = t + _WD
                while tt < scan_end and _weather_at(zone, tt) not in rain_weathers:
                    tt += _WD
                dry_end = tt
                if dry_end > now:
                    result.append((trigger, dry_end))
                    if len(result) >= n:
                        break
                t = tt          # 跳過整段乾燥，避免重複記錄
                dry_start = None
                continue
        else:
            dry_start = None

        t += _WD

    return result


def _next_weather_match_windows(
    zone: str,
    weathers: set[str],
    n: int = 3,
) -> list[tuple[int, int]]:
    """找下 n 個天氣符合 weathers 的 1400s 窗口。"""
    now      = int(time.time())
    scan_end = now + 30 * 86400
    result: list[tuple[int, int]] = []
    t = _window_start(now)

    while t < scan_end:
        if _weather_at(zone, t) in weathers:
            ws = max(t, now)
            result.append((ws, t + _WD))
            if len(result) >= n:
                break
        t += _WD

    return result


def _next_et_weather_windows(
    h_s: int,
    h_e: int,
    zone: str,
    weathers: set[str],
    n: int = 3,
) -> list[tuple[int, int]]:
    """
    ET [h_s, h_e) 時段內，且天氣符合 weathers 的 1400s 窗口。
    跨午夜：h_e < h_s。
    """
    now      = int(time.time())
    scan_end = now + 60 * 86400
    result: list[tuple[int, int]] = []
    t = _window_start(now)

    units_per_day = _ET_DAY // _WD   # 3

    while t < scan_end:
        et = _parse_et_now(t)
        h  = et["hour"]

        in_window = (h >= h_s) if (h_e < h_s) else (h_s <= h < h_e)
        # 跨午夜補充：h < h_e 也算（次日部分）
        if h_e <= h_s:
            in_window = (h >= h_s) or (h < h_e)

        if in_window and _weather_at(zone, t) in weathers:
            ws = max(t, now)
            result.append((ws, t + _WD))
            if len(result) >= n:
                break

        t += _WD

    return result


def _next_moonphase_et_weather_windows(
    day_s: int,
    day_e: int,
    h_s: int,
    h_e: int,
    zone: str,
    weathers: set[str],
    n: int = 3,
) -> list[tuple[int, int]]:
    """
    月相日（day_s~day_e）+ ET 時段 [h_s, h_e) + 天氣符合 weathers。
    回傳 1400s 窗口。
    """
    now    = int(time.time())
    result: list[tuple[int, int]] = []

    et_now = _parse_et_now(now)
    year   = et_now["year"]
    month  = et_now["month"]

    for _ in range(60):
        for day in range(day_s, day_e + 1):
            day_base = _et_day_unix(year, month, day)
            # 每個天氣窗口：每ET日3個
            for slot in range(3):
                ws = day_base + slot * _WD
                et = _parse_et_now(ws)
                h  = et["hour"]

                if h_e <= h_s:
                    in_win = (h >= h_s) or (h < h_e)
                else:
                    in_win = h_s <= h < h_e

                if in_win and _weather_at(zone, ws) in weathers and ws + _WD > now:
                    result.append((max(ws, now), ws + _WD))
                    if len(result) >= n:
                        return result

        year, month = _adj_et_month(year, month, 1)

    return result


# ── Embed 建構 ────────────────────────────────────────────────────────────────

def _fmt_windows(windows: list[tuple[int, int]]) -> str:
    if not windows:
        return "（近期無窗口）"
    now = int(time.time())
    lines = []
    for s, e in windows:
        if s <= now < e:
            lines.append(f"<t:{s}:s> ～ <t:{e}:s>")
        else:
            lines.append(f"<t:{s}:s> ～ <t:{e}:s>")
    return "\n".join(lines)


def _is_open(windows: list[tuple[int, int]]) -> bool:
    """Check if the first window is currently active."""
    if not windows:
        return False
    now = int(time.time())
    s, e = windows[0]
    return s <= now < e


def _next_rel(windows: list[tuple[int, int]]) -> str:
    """回傳相對時間標籤；無窗口則空字串。"""
    if not windows:
        return ""
    now = int(time.time())
    s, e = windows[0]
    if s <= now < e:
        return f" ｜ <t:{e}:R> 結束"
    return f" ｜ <t:{s}:R>"


def build_srank_special_embed() -> discord.Embed:
    now  = int(time.time())
    embed = discord.Embed(
        title="✨ 特殊 S 怪生成條件",
        description=f"更新：<t:{now}:t>　　下 2 筆時間窗口（現實時間）",
        color=discord.Color.gold(),
    )

    # ── 純 ET 時段組 ──────────────────────────────────────────────────────────
    # 1. 千竿口花希達 — ET17:00-21:00
    wins_b = _next_et_windows(17, 21, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_b) else ''}__**千竿口花希達｜黑衣森林北部林區**__{_next_rel(wins_b)}",
        value="ET17:00–21:00\n" + _fmt_windows(wins_b),
        inline=False,
    )

    # 2. 火憤牛 — ET8:00-11:00
    wins_f = _next_et_windows(8, 11, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_f) else ''}__**火憤牛｜西拉諾西亞**__{_next_rel(wins_f)}",
        value="ET8:00–11:00\n" + _fmt_windows(wins_f),
        inline=False,
    )

    # 3. 護土精靈 — ET19:00-22:00（中拉諾西亞）
    wins_g = _next_et_windows(19, 22, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_g) else ''}__**護土精靈｜中拉諾西亞**__{_next_rel(wins_g)}",
        value="ET19:00–22:00\n" + _fmt_windows(wins_g),
        inline=False,
    )

    # 4. 伽瑪 — ET17:00→次日08:00
    wins_i = _next_et_windows(17, 8, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_i) else ''}__**伽瑪｜延夏**__{_next_rel(wins_i)}",
        value="ET17:00 → 08:00\n" + _fmt_windows(wins_i),
        inline=False,
    )

    # ── 分隔線 ─────────────────────────────────────────────────────────────────
    embed.add_field(name="─" * 34, value="", inline=False)

    # ── 特殊條件組 ────────────────────────────────────────────────────────────
    # 5. 奪心魔 — 新月 day1-4, ET17:00→3:00, day1 從 0:00 起
    wins_a = _next_moonphase_et_windows(1, 4, 17, 3, first_day_h_s=0, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_a) else ''}__**奪心魔｜黑衣森林南部林區**__{_next_rel(wins_a)}",
        value="新月 | ET17:00→3:00（首日0:00起）\n" + _fmt_windows(wins_a),
        inline=False,
    )

    # 6. 雷德羅巨蛇 — 連續30分鐘小雨（黑衣森林中央林區）
    wins_c = _continuous_weather_windows("黑衣森林中央林區", {"小雨"}, 30, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_c) else ''}__**雷德羅巨蛇｜黑衣森林中央林區**__{_next_rel(wins_c)}",
        value="連續30分鐘「小雨」\n" + _fmt_windows(wins_c),
        inline=False,
    )

    # 7. 咕爾呱洛斯 — 滿月 day16-20, ET17:00→3:00
    wins_d = _next_moonphase_et_windows(16, 20, 17, 3, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_d) else ''}__**咕爾呱洛斯｜拉諾西亞低地**__{_next_rel(wins_d)}",
        value="滿月 | ET17:00→3:00\n" + _fmt_windows(wins_d),
        inline=False,
    )

    # 8. 伽洛克 — 連續200分鐘不下雨（東拉諾西亞）
    wins_e = _continuous_no_weather_windows("東拉諾西亞", {"小雨", "暴雨"}, 200, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_e) else ''}__**伽洛克｜東拉諾西亞**__{_next_rel(wins_e)}",
        value="連續200現實分鐘非雨天\n" + _fmt_windows(wins_e),
        inline=False,
    )

    # 9. 虛無探索者 — 天氣碧空/晴朗（西薩納蘭）
    wins_h = _next_weather_match_windows("西薩納蘭", {"碧空", "晴朗"}, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_h) else ''}__**虛無探索者｜西薩納蘭**__{_next_rel(wins_h)}",
        value="天氣：「碧空」 / 「晴朗」\n" + _fmt_windows(wins_h),
        inline=False,
    )

    # 10. 巨大鰩 — 滿月 day16 12:00 → day21 00:00 連續段
    wins_j = _next_moonphase_continuous_window(16, 20, 12, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_j) else ''}__**巨大鰩｜紅玉海**__{_next_rel(wins_j)}",
        value="滿月 | 首日 12:00 起\n" + _fmt_windows(wins_j),
        inline=False,
    )

    # 11. 布弗魯 — ET9:00-17:00 且 天氣碧空/晴朗（迷津）
    wins_k = _next_et_weather_windows(9, 17, "迷津", {"碧空", "晴朗"}, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_k) else ''}__**布弗魯｜迷津**__{_next_rel(wins_k)}",
        value="ET9:00–17:00 | 天氣：「碧空」 / 「晴朗」\n" + _fmt_windows(wins_k),
        inline=False,
    )

    # 12. 厭忌之人奇里格 — 新月 day1-4, ET0:00-8:00, 天氣薄霧（奧闊帕恰山）
    wins_l = _next_moonphase_et_weather_windows(1, 4, 0, 8, "奧闊帕恰山", {"薄霧"}, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_l) else ''}__**厭忌之人奇里格｜奧闊帕恰山**__{_next_rel(wins_l)}",
        value="新月 | ET0:00–8:00 | 天氣：「薄霧」\n" + _fmt_windows(wins_l),
        inline=False,
    )

    embed.set_footer(text="每 3 分鐘自動刷新 | 時間以本地時區顯示")
    return embed


# ── 狀態讀寫 ──────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_state(guild_id: int, channel_id: int, message_id: int) -> None:
    data = _load_state()
    data[str(guild_id)] = {"channel_id": channel_id, "message_id": message_id}
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Persistent Refresh View ──────────────────────────────────────────────────

class SRankRefreshView(discord.ui.View):
    """含「手動刷新」按鈕的持久化 View，bot 重啟後仍可響應點擊。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔄 手動刷新", style=discord.ButtonStyle.secondary, custom_id="srank_refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(embed=build_srank_special_embed(), view=self)


# ── Cog ───────────────────────────────────────────────────────────────────────

class SRankSpecial(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._panels: dict[int, tuple[int, int]] = {}  # guild_id → (channel_id, message_id)
        self._view = SRankRefreshView()
        # 從持久化載入
        for guild_id_str, v in _load_state().items():
            self._panels[int(guild_id_str)] = (v["channel_id"], v["message_id"])
        self.auto_refresh.start()

    def cog_unload(self):
        self.auto_refresh.cancel()

    @tasks.loop(seconds=180)
    async def auto_refresh(self):
        for guild_id, (channel_id, message_id) in list(self._panels.items()):
            try:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=build_srank_special_embed(), view=self._view)
            except Exception as e:
                print(f"[SRankSpecial] guild={guild_id} 刷新失敗: {e!r}")

    @auto_refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="set_srank_timer_channel", description="在此頻道設定特殊 S 怪生成條件計時器面板")
    @app_commands.guild_only()
    @is_allowed("set_srank_timer_channel")
    async def set_srank_timer_channel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id

        # 刪除該伺服器既有的面板（一個伺服器只能有一個）
        if guild_id in self._panels:
            old_ch_id, old_msg_id = self._panels[guild_id]
            try:
                old_ch = self.bot.get_channel(old_ch_id) or await self.bot.fetch_channel(old_ch_id)
                old_msg = await old_ch.fetch_message(old_msg_id)
                await old_msg.delete()
            except Exception:
                pass  # 訊息已不存在，忽略

        embed = build_srank_special_embed()
        msg   = await interaction.channel.send(embed=embed, view=self._view)
        self._panels[guild_id] = (interaction.channel_id, msg.id)
        _save_state(guild_id, interaction.channel_id, msg.id)
        await interaction.followup.send("✅ 特殊 S 怪計時器面板已設定，每 5 分鐘自動刷新。", ephemeral=True)


async def setup(bot: commands.Bot):
    cog = SRankSpecial(bot)
    await bot.add_cog(cog)
    # 向 discord.py 註冊持久化 View，bot 重啟後仍可響應按鈕點擊
    bot.add_view(cog._view)
