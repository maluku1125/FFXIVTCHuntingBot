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
import random
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
_NOTIFY_STATE_FILE = os.path.normpath(os.path.join(_ROOT_DIR, "Config", "srank_special_notify_state.json"))
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
    first_day_h_s: 首日從此小時開始，額外加一筆 (first_day_h_s ~ h_e) 的短窗口。
    h_e < h_s 表示跨午夜。
    最後一天的跨午夜窗口截止於 day_e+1 00:00（月相範圍結束）。
    回傳 [(real_start, real_end), ...]
    """
    now     = int(time.time())
    et_now  = _parse_et_now(now)
    result: list[tuple[int, int]] = []

    year  = et_now["year"]
    month = et_now["month"]
    wrap  = h_e <= h_s  # 跨午夜

    for _ in range(30):  # 最多掃 30 個 ET 月
        for day in range(day_s, day_e + 1):
            day_base    = _et_day_unix(year, month, day)
            is_last_day = (day == day_e)

            _eths = _ET_DAY // 24  # 175s / ET hour

            # ── 首日特殊短窗口：first_day_h_s → h_e（不跨午夜） ───────────
            if day == day_s and first_day_h_s is not None:
                s0 = day_base + first_day_h_s * _eths
                e0 = day_base + h_e * _eths
                if e0 > now:
                    result.append((s0, e0))
                    if len(result) >= n:
                        return result

            # ── 當日主窗口：h_s → h_e（可跨午夜） ──────────────────────────
            real_s = day_base + h_s * _eths
            if wrap:
                # 最後一天：截止在 day_e+1 00:00，不延伸到 h_e
                real_e = (day_base + _ET_DAY) if is_last_day else (day_base + _ET_DAY + h_e * _eths)
            else:
                real_e = day_base + h_e * _eths

            if real_e > now:
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
    用於 i. 巨大魟：day16 12:00 → day21 00:00
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
    t = _window_start(now - 18 * 3600)  # 回溯 18 小時，避免遺漏已進行中的連續段
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
    t = _window_start(now - 18 * 3600)  # 回溯 18 小時，避免遺漏已進行中的連續段
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
    """找下 n 段連續符合 weathers 的天氣區間（合併相鄰連續格）。"""
    now      = int(time.time())
    scan_end = now + 30 * 86400
    result: list[tuple[int, int]] = []
    t = _window_start(now)

    while t < scan_end:
        if _weather_at(zone, t) in weathers:
            seg_start = t
            # 向後合併連續符合的天氣格
            while t + _WD < scan_end and _weather_at(zone, t + _WD) in weathers:
                t += _WD
            result.append((seg_start, t + _WD))
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
    逐 ET 日掃描：在 ET h_s 時刻檢查天氣，符合時回傳完整的
    (ET h_s 現實秒, ET h_e 現實秒) 窗口。
    跨午夜：h_e < h_s，h_e 對應次 ET 日。
    """
    _ETHS    = _ET_DAY // 24  # 175s / ET hour
    now      = int(time.time())
    et_now   = _parse_et_now(now)
    result: list[tuple[int, int]] = []

    year    = et_now["year"]
    month   = et_now["month"]
    day_idx = et_now["day"]

    for _ in range(600):  # 最多掃 600 ET 日（約 50 ET 月）
        day_base = _et_day_unix(year, month, day_idx)
        real_s   = day_base + h_s * _ETHS
        if h_e > h_s:
            real_e = day_base + h_e * _ETHS
        else:  # 跨午夜
            real_e = day_base + _ET_DAY + h_e * _ETHS

        if real_e > now and _weather_at(zone, real_s) in weathers:
            result.append((real_s, real_e))
            if len(result) >= n:
                break

        day_idx += 1
        if day_idx > _ET_MONTH:
            day_idx = 1
            year, month = _adj_et_month(year, month, 1)

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
                    result.append((ws, ws + _WD))
                    if len(result) >= n:
                        return result

        year, month = _adj_et_month(year, month, 1)

    return result


# ── 訂閱通知：怪物映射 & 狀態 IO ──────────────────────────────────────────────

# 全部需訂閱通知的特殊 S 怪，value 為回傳 [(win_start, win_end), ...] 的 lambda
_NOTIFY_MONSTERS: dict[str, callable] = {
    # ── 上區（純 ET 時段 / 天氣窗口，5 分鐘前通知）──
    "千竿食腐獸希達": lambda: _next_et_windows(17, 21, n=5),
    "虛無探索者":     lambda: _next_weather_match_windows("西薩納蘭", {"碧空", "晴朗"}, n=5),
    "火憤牛":         lambda: _next_et_windows(8, 11, n=5),
    "護土精靈":       lambda: _next_et_windows(19, 22, n=5),
    "伽瑪":           lambda: _next_et_windows(17, 8, n=5),
    "布弗魯":         lambda: _next_et_weather_windows(9, 17, "迷津", {"碧空", "晴朗"}, n=5),
    # ── 下區（特殊條件，1 小時前通知）──
    "精神吸取者":     lambda: _next_moonphase_et_windows(1, 4, 17, 3, first_day_h_s=0, n=5),
    "雷德羅巨蛇":    lambda: _continuous_weather_windows("黑衣森林中央林區", {"小雨"}, 30, n=5),
    "凱羅葛洛斯":    lambda: _next_moonphase_et_windows(16, 20, 17, 3, n=5),
    "伽洛克":        lambda: _continuous_no_weather_windows("東拉諾西亞", {"小雨", "暴雨"}, 200, n=5),
    "巨大魟":        lambda: _next_moonphase_continuous_window(16, 20, 12, n=5),
    "厭忌之人奇里格": lambda: _next_moonphase_et_weather_windows(1, 4, 0, 8, "奧闊帕恰山", {"薄霧"}, n=5),
}

# 上區怪物（5 分鐘前預告）
_SHORT_WARN_KEYS: frozenset[str] = frozenset({
    "千竿食腐獸希達", "虛無探索者", "火憤牛", "護土精靈", "伽瑪", "布弗魯",
})

# 怪物 key → 顯示用全名（含地點）
_NOTIFY_LABELS: dict[str, str] = {
    "千竿食腐獸希達": "千竿食腐獸希達｜黑衣森林北部林區",
    "虛無探索者":     "虛無探索者｜西薩納蘭",
    "火憤牛":         "火憤牛｜西拉諾西亞",
    "護土精靈":       "護土精靈｜中拉諾西亞",
    "伽瑪":           "伽瑪｜延夏",
    "布弗魯":         "布弗魯｜迷津",
    "精神吸取者":     "精神吸取者｜黑衣森林南部林區",
    "雷德羅巨蛇":    "雷德羅巨蛇｜黑衣森林中央林區",
    "凱羅葛洛斯":    "凱羅葛洛斯｜拉諾西亞低地",
    "伽洛克":        "伽洛克｜東拉諾西亞",
    "巨大魟":        "巨大魟｜紅玉海",
    "厭忌之人奇里格": "厭忌之人奇里格｜奧闊帕恰山",
}

_NOTIFY_STATE_DEFAULT = {k: {"warned": [], "opened": [], "pending_delete": []} for k in _NOTIFY_MONSTERS}


def _load_notify_state() -> dict:
    if os.path.exists(_NOTIFY_STATE_FILE):
        try:
            with open(_NOTIFY_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 向下相容：pending_delete 舊條目缺 kind 時預設 "open"
            for guild_data in data.values():
                if isinstance(guild_data, dict):
                    for k_data in guild_data.values():
                        if isinstance(k_data, dict):
                            for entry in k_data.get("pending_delete", []):
                                if "kind" not in entry:
                                    entry["kind"] = "open"
            return data
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_notify_state(state: dict) -> None:
    os.makedirs(os.path.dirname(_NOTIFY_STATE_FILE), exist_ok=True)
    with open(_NOTIFY_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
    # 1. 千竿食腐獸希達 — ET17:00-21:00
    wins_b = _next_et_windows(17, 21, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_b) else '💤 '}**千竿食腐獸希達｜黑衣森林北部林區**{_next_rel(wins_b)}",
        value="ET17:00–21:00\n" + _fmt_windows(wins_b),
        inline=False,
    )

    # 2. 虛無探索者 — 天氣碧空/晴朗（西薩納蘭）
    wins_h = _next_weather_match_windows("西薩納蘭", {"碧空", "晴朗"}, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_h) else '💤 '}**虛無探索者｜西薩納蘭**{_next_rel(wins_h)}",
        value="天氣：「碧空」 / 「晴朗」\n" + _fmt_windows(wins_h),
        inline=False,
    )

    # 3. 火憤牛 — ET8:00-11:00
    wins_f = _next_et_windows(8, 11, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_f) else '💤 '}**火憤牛｜西拉諾西亞**{_next_rel(wins_f)}",
        value="ET8:00–11:00\n" + _fmt_windows(wins_f),
        inline=False,
    )

    # 4. 護土精靈 — ET19:00-22:00（中拉諾西亞）
    wins_g = _next_et_windows(19, 22, n=2)
    _g_name = "護士精靈" if random.random() < 0.05 else "護土精靈"
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_g) else '💤 '}**{_g_name}｜中拉諾西亞**{_next_rel(wins_g)}",
        value="ET19:00–22:00\n" + _fmt_windows(wins_g),
        inline=False,
    )

    # 5. 伽瑪 — ET17:00→次日08:00
    wins_i = _next_et_windows(17, 8, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_i) else '💤 '}**伽瑪｜延夏**{_next_rel(wins_i)}",
        value="ET17:00 → 08:00\n" + _fmt_windows(wins_i),
        inline=False,
    )

    # 6. 布弗魯 — ET9:00-17:00 且 天氣碧空/晴朗（迷津）
    wins_k = _next_et_weather_windows(9, 17, "迷津", {"碧空", "晴朗"}, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_k) else '💤 '}**布弗魯｜迷津**{_next_rel(wins_k)}",
        value="ET9:00–17:00 | 天氣：「碧空」 / 「晴朗」\n" + _fmt_windows(wins_k),
        inline=False,
    )

    # ── 分隔線 ─────────────────────────────────────────────────────────────────
    embed.add_field(name="─" * 34, value="", inline=False)

    # ── 特殊條件組 ────────────────────────────────────────────────────────────
    # 7. 精神吸取者 — 新月 day1-4, ET17:00→3:00, day1 從 0:00 起
    wins_a = _next_moonphase_et_windows(1, 4, 17, 3, first_day_h_s=0, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_a) else '💤 '}**精神吸取者｜黑衣森林南部林區**{_next_rel(wins_a)}",
        value="新月 | ET17:00→3:00（首日0:00起）\n" + _fmt_windows(wins_a),
        inline=False,
    )

    # 8. 雷德羅巨蛇 — 連續30分鐘小雨（黑衣森林中央林區）
    wins_c = _continuous_weather_windows("黑衣森林中央林區", {"小雨"}, 30, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_c) else '💤 '}**雷德羅巨蛇｜黑衣森林中央林區**{_next_rel(wins_c)}",
        value="連續30分鐘「小雨」\n" + _fmt_windows(wins_c),
        inline=False,
    )

    # 9. 凱羅葛洛斯 — 滿月 day16-20, ET17:00→3:00
    wins_d = _next_moonphase_et_windows(16, 20, 17, 3, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_d) else '💤 '}**凱羅葛洛斯｜拉諾西亞低地**{_next_rel(wins_d)}",
        value="滿月 | ET17:00→3:00\n" + _fmt_windows(wins_d),
        inline=False,
    )

    # 10. 伽洛克 — 連續200分鐘不下雨（東拉諾西亞）
    wins_e = _continuous_no_weather_windows("東拉諾西亞", {"小雨", "暴雨"}, 200, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_e) else '💤 '}**伽洛克｜東拉諾西亞**{_next_rel(wins_e)}",
        value="連續200現實分鐘非雨天\n" + _fmt_windows(wins_e),
        inline=False,
    )

    # 11. 巨大魟 — 滿月 day16 12:00 → day21 00:00 連續段
    wins_j = _next_moonphase_continuous_window(16, 20, 12, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_j) else '💤 '}**巨大魟｜紅玉海**{_next_rel(wins_j)}",
        value="滿月 | 首日 12:00 起\n" + _fmt_windows(wins_j),
        inline=False,
    )

    # 12. 厭忌之人奇里格 — 新月 day1-4, ET0:00-8:00, 天氣薄霧（奧闊帕恰山）
    wins_l = _next_moonphase_et_weather_windows(1, 4, 0, 8, "奧闊帕恰山", {"薄霧"}, n=2)
    embed.add_field(
        name=f"{'⚡ ' if _is_open(wins_l) else '💤 '}**厭忌之人奇里格｜奧闊帕恰山**{_next_rel(wins_l)}",
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

class SRankSubscribeSelect(discord.ui.Select):
    """讓使用者訂閱 / 取消訂閱特殊 S 怪窗口通知的 Select Menu（persistent）。"""

    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key)
            for key, label in _NOTIFY_LABELS.items()
        ]
        super().__init__(
            custom_id="srank_special_subscribe_v1",
            placeholder="📋 訂閱窗口通知（再次選擇即取消）",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("❌ 此功能僅限伺服器內使用。", ephemeral=True)
            return

        cfg = configparser.ConfigParser()
        cfg.read(_CONFIG_PATH, encoding="utf-8")

        key = self.values[0]
        role_id_str = cfg.get("srank_special_roles", key, fallback="").strip()
        if not role_id_str or not role_id_str.isdigit():
            await interaction.response.send_message(
                f"❌ 管理員尚未設定「{key}」的訂閱身分組，請聯繫管理員。", ephemeral=True
            )
            return

        role = interaction.guild.get_role(int(role_id_str))
        if role is None:
            await interaction.response.send_message(
                f"❌ 找不到身分組（ID: {role_id_str}），請確認 ini 設定正確。", ephemeral=True
            )
            return

        _separator_role_id_str = cfg.get("srank_special_notify", "separator_role", fallback="").strip()
        separator_role = (
            interaction.guild.get_role(int(_separator_role_id_str))
            if _separator_role_id_str.isdigit() else None
        )

        member = interaction.user
        if role in member.roles:
            await member.remove_roles(role, reason="SRank Special 訂閱取消")
            # 確認移除後是否還擁有任何通知身分組，若無則一併移除分隔身分組
            all_notify_ids = set()
            for k in _NOTIFY_MONSTERS:
                rid_str = cfg.get("srank_special_roles", k, fallback="").strip()
                if rid_str.isdigit():
                    all_notify_ids.add(int(rid_str))
            all_notify_ids.discard(int(role_id_str))  # 剛移除的不算
            has_any = any(r.id in all_notify_ids for r in member.roles)
            if not has_any and separator_role and separator_role in member.roles:
                await member.remove_roles(separator_role, reason="SRank Special 無訂閱，移除分隔身分組")
            msg = f"✅ 已取消訂閱 **{_NOTIFY_LABELS[key]}**。"
        else:
            await member.add_roles(role, reason="SRank Special 訂閱")
            # 加入分隔身分組（若尚未擁有）
            if separator_role and separator_role not in member.roles:
                await member.add_roles(separator_role, reason="SRank Special 訂閱，加入分隔身分組")
            msg = (
                f"✅ 已訂閱 **{_NOTIFY_LABELS[key]}**！\n"
                f"窗口開啟前 1 小時及開窗當下將收到 @tag 通知。"
            )
        # edit_message 作為 interaction response，讓選單立即回到 placeholder 狀態
        # followup 送 ephemeral 反饋給使用者
        await interaction.response.edit_message(embed=build_srank_special_embed(), view=SRankRefreshView())
        await interaction.followup.send(msg, ephemeral=True)


class SRankRefreshView(discord.ui.View):
    """含「手動刷新」按鈕與訂閱選單的持久化 View，bot 重啟後仍可響應互動。"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SRankSubscribeSelect())

    @discord.ui.button(label="🔄 手動刷新", style=discord.ButtonStyle.secondary, custom_id="srank_refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(embed=build_srank_special_embed(), view=self)


# ── Cog ───────────────────────────────────────────────────────────────────────

class SRankSpecial(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._panels: dict[int, tuple[int, int]] = {}   # guild_id → (channel_id, message_id)
        self._view = SRankRefreshView()
        self._notify_state: dict = _load_notify_state()
        # 從持久化載入面板位置
        for guild_id_str, v in _load_state().items():
            self._panels[int(guild_id_str)] = (v["channel_id"], v["message_id"])
        self.auto_refresh.start()
        self.notify_check.start()

    def cog_unload(self):
        self.auto_refresh.cancel()
        self.notify_check.cancel()

    @tasks.loop(seconds=180)
    async def auto_refresh(self):
        for guild_id, (channel_id, message_id) in list(self._panels.items()):
            try:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)
                msg = await channel.fetch_message(message_id)
                await msg.edit(embed=build_srank_special_embed(), view=self._view)
            except discord.NotFound:
                print(f"[SRankSpecial] guild={guild_id} 頻道或訊息已不存在，自動移除面板記錄。")
                self._panels.pop(guild_id, None)
                data = _load_state()
                data.pop(str(guild_id), None)
                os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
                with open(_STATE_FILE, "w", encoding="utf-8") as _f:
                    json.dump(data, _f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[SRankSpecial] guild={guild_id} 刷新失敗: {e!r}")

    @auto_refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def notify_check(self):
        if not self._panels:
            return

        cfg = configparser.ConfigParser()
        cfg.read(_CONFIG_PATH, encoding="utf-8")

        notify_guild_str = cfg.get("srank_special_notify", "notify_guild", fallback="").strip()
        notify_guild_id  = int(notify_guild_str) if notify_guild_str.isdigit() else None

        now = int(time.time())
        state_changed = False

        for guild_id, (channel_id, _) in list(self._panels.items()):
            if notify_guild_id is not None and guild_id != notify_guild_id:
                continue  # 不在允許清單內，跳過

            guild_id_str = str(guild_id)

            # 動態補建該 guild 的 state 結構
            if guild_id_str not in self._notify_state:
                self._notify_state[guild_id_str] = {
                    k: {"warned": [], "opened": [], "pending_delete": []} for k in _NOTIFY_MONSTERS
                }
                state_changed = True
            else:
                for k in _NOTIFY_MONSTERS:
                    if k not in self._notify_state[guild_id_str]:
                        self._notify_state[guild_id_str][k] = {"warned": [], "opened": [], "pending_delete": []}
                        state_changed = True
                    for sub in ("warned", "opened", "pending_delete"):
                        if sub not in self._notify_state[guild_id_str][k]:
                            self._notify_state[guild_id_str][k][sub] = []
                            state_changed = True

            g_state = self._notify_state[guild_id_str]

            # 取得面板頻道
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except Exception as e:
                    print(f"[SRankSpecial notify] guild={guild_id} 無法取得面板頻道 (ID: {channel_id}): {e!r}")
                    continue

            # 清理 30 天前的舊記錄
            cutoff = now - 30 * 86400
            for k in g_state:
                for sub in ("warned", "opened"):
                    before = len(g_state[k][sub])
                    g_state[k][sub] = [ts for ts in g_state[k][sub] if ts > cutoff]
                    if len(g_state[k][sub]) != before:
                        state_changed = True

            # 刪除已關窗的 @tag 訊息（pending_delete 中 win_end <= now 的全部）
            for k in list(g_state.keys()):
                to_del = [e for e in g_state[k].get("pending_delete", []) if e["win_end"] <= now]
                keep   = [e for e in g_state[k].get("pending_delete", []) if e["win_end"] >  now]
                failed = []
                for entry in to_del:
                    try:
                        del_msg = await channel.fetch_message(entry["msg_id"])
                        await del_msg.delete()
                        print(f"[SRankSpecial notify] guild={guild_id} 已刪除 {k} 的關窗通知 (msg_id={entry['msg_id']})")
                    except discord.NotFound:
                        pass  # 訊息已不存在，無需重試
                    except Exception as e:
                        print(f"[SRankSpecial notify] guild={guild_id} 刪除通知訊息失敗，留待下次重試 ({k}, msg_id={entry['msg_id']}): {e!r}")
                        failed.append(entry)  # 暫時保留，下次 tick 重試
                if to_del:
                    g_state[k]["pending_delete"] = keep + failed
                    state_changed = True

            for key, win_fn in _NOTIFY_MONSTERS.items():
                role_id_str = cfg.get("srank_special_roles", key, fallback="").strip()
                if not role_id_str or not role_id_str.isdigit():
                    print(f"[SRankSpecial notify] 跳過 {key}：未設定身分組。")
                    continue

                role_id = int(role_id_str)
                warn_secs = 300 if key in _SHORT_WARN_KEYS else 3600  # 上區5分鐘，下區1小時

                try:
                    windows = win_fn()
                except Exception as e:
                    print(f"[SRankSpecial notify] {key} 窗口計算失敗: {e!r}")
                    continue

                for (win_start, win_end) in windows:
                    if win_end <= now:
                        continue

                    # ① 預告通知：窗口開始前 warn_secs
                    if win_start - warn_secs <= now < win_start:
                        if win_start not in g_state[key]["warned"]:
                            try:
                                sent = await channel.send(
                                    f"<@&{role_id}> ⏰ 窗口將於 <t:{win_start}:R> 開啟！"
                                )
                                g_state[key]["pending_delete"].append({"msg_id": sent.id, "win_end": win_end, "kind": "warn"})
                                print(f"[SRankSpecial notify] guild={guild_id} 預告通知已發送 ({key}) win_start={win_start}")
                            except Exception as e:
                                print(f"[SRankSpecial notify] guild={guild_id} 預告通知發送失敗 ({key}): {e!r}")
                            g_state[key]["warned"].append(win_start)
                            state_changed = True

                    # ② 開窗通知：窗口已開始，先刪除同窗口的預告訊息
                    # 用 win_end 作為去重 key：天氣類窗口每個 1400s 格的 win_start 都不同，
                    # 但同一連續天氣序列的 win_end 相同，避免每格都重複送出通知。
                    elif win_start <= now < win_end:
                        if win_end not in g_state[key]["opened"]:
                            # 刪除同 win_start 的預告訊息（kind="warn"）
                            warn_entries = [
                                e for e in g_state[key]["pending_delete"]
                                if e.get("kind") == "warn" and e["win_end"] == win_end
                            ]
                            remaining = [
                                e for e in g_state[key]["pending_delete"]
                                if not (e.get("kind") == "warn" and e["win_end"] == win_end)
                            ]
                            for entry in warn_entries:
                                try:
                                    del_msg = await channel.fetch_message(entry["msg_id"])
                                    await del_msg.delete()
                                    print(f"[SRankSpecial notify] guild={guild_id} 開窗時刪除預告訊息 ({key}, msg_id={entry['msg_id']})")
                                except Exception as e:
                                    print(f"[SRankSpecial notify] guild={guild_id} 刪除預告訊息失敗 ({key}): {e!r}")
                            g_state[key]["pending_delete"] = remaining
                            try:
                                sent = await channel.send(
                                    f"<@&{role_id}> 🔔 窗口已開啟！<t:{win_end}:R> 結束"
                                )
                                g_state[key]["pending_delete"].append({"msg_id": sent.id, "win_end": win_end, "kind": "open"})
                                print(f"[SRankSpecial notify] guild={guild_id} 開窗通知已發送 ({key}) win_start={win_start} win_end={win_end}")
                            except Exception as e:
                                print(f"[SRankSpecial notify] guild={guild_id} 開窗通知發送失敗 ({key}): {e!r}")
                            g_state[key]["opened"].append(win_end)
                            state_changed = True

                    # 超過 warn_secs 以上的未來窗口，本次不處理

        if state_changed:
            _save_notify_state(self._notify_state)

    @notify_check.before_loop
    async def before_notify_check(self):
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
    # 向 discord.py 註冊持久化 View，bot 重啟後仍可響應按鈕與選單互動
    bot.add_view(cog._view)
