"""
Weather.py — FFXIV 天氣預報指令
算法移植自 https://github.com/Asvel/ffxiv-weather (MIT License)
每個天氣時段 = 8 ET hours = 8 × 175 = 1400 現實秒
1 ET 日 = 3 天氣時段 = 4200 現實秒
"""
import json
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

# ── 資料載入 ──────────────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_FILE = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "Data", "weather_data.json"))

with open(_DATA_FILE, "r", encoding="utf-8") as _f:
    _RAW = json.load(_f)

ZONE_DATA: dict    = _RAW["zones"]
REGION_DATA: dict  = _RAW["regions"]
_ZONE_TO_REGION: dict[str, str] = {
    z: r for r, zones in REGION_DATA.items() for z in zones
}

WEATHER_EMOJI: dict[str, str] = {
    "碧空":     "☀️",
    "晴朗":     "🌤️",
    "陰雲":     "⛅",
    "薄霧":     "🌫️",
    "妖霧":     "🌫️",
    "小雨":     "🌧️",
    "暴雨":     "🌦️",
    "雷雨":     "⛈️",
    "打雷":     "🌩️",
    "小雪":     "❄️",
    "暴雪":     "🌨️",
    "強風":     "💨",
    "微風":     "🍃",
    "靈風":     "🌀",
    "揚沙":     "🌪️",
    "熱浪":     "🔥",
    "幽暗":     "🌑",
    "靈電":     "⚡",
    "磁暴":     "🌌",
    "月塵":     "🌕",
    "大氣幻象": "🔮",
    "幻影擾動": "🔮",
}

WEATHER_DURATION = 1400   # 現實秒 / 天氣時段
_ET_DAY_REAL     = 4200   # 現實秒 / ET 日（= 3 × 1400）
_ET_DAYS_MONTH   = 32     # ET 日 / 月
_ET_MONTHS_YEAR  = 12
# 月相：每 4 ET 日換一相，共 8 相（各 2 字，方便對齊）
_MOON_PHASES = ["新月", "上弦", "半月", "盈凸", "滿月", "虧凸", "下弦", "殘月"]


# ── 算法核心（移植自 SaintCoinach / Asvel/ffxiv-weather）─────────────────────
def _forecast_target(unix_seconds: int) -> int:
    bell       = unix_seconds // 175
    increment  = (bell + 8 - (bell % 8)) % 24
    total_days = (unix_seconds // 4200) & 0xFFFFFFFF
    calc_base  = total_days * 100 + increment
    step1      = ((calc_base << 11) ^ calc_base) & 0xFFFFFFFF
    step2      = ((step1 >> 8) ^ step1) & 0xFFFFFFFF
    return step2 % 100


def _get_weather(zone_zh: str, unix_seconds: int) -> str:
    target = _forecast_target(unix_seconds)
    for threshold, weather in ZONE_DATA[zone_zh]["rates"]:
        if target < threshold:
            return weather
    return ZONE_DATA[zone_zh]["rates"][-1][1]


# ── ET 月份工具 ───────────────────────────────────────────────────────────────
def _unix_to_et_ym(unix_seconds: int) -> tuple[int, int]:
    """現實 Unix 秒 → (ET年, ET月)。"""
    day_of_era = unix_seconds // _ET_DAY_REAL
    et_year  = day_of_era // (_ET_DAYS_MONTH * _ET_MONTHS_YEAR) + 1
    et_month = (day_of_era // _ET_DAYS_MONTH) % _ET_MONTHS_YEAR + 1
    return et_year, et_month


def _et_month_start_unix(et_year: int, et_month: int) -> int:
    """ET 年月第 1 天 ET 00:00 的現實 Unix 秒。"""
    day_of_era = (et_year - 1) * _ET_DAYS_MONTH * _ET_MONTHS_YEAR + (et_month - 1) * _ET_DAYS_MONTH
    return day_of_era * _ET_DAY_REAL


def _adj_et_month(et_year: int, et_month: int, delta: int) -> tuple[int, int]:
    """ET 月份加減（跨年自動處理）。"""
    total = (et_year - 1) * _ET_MONTHS_YEAR + (et_month - 1) + delta
    return total // _ET_MONTHS_YEAR + 1, total % _ET_MONTHS_YEAR + 1


# ── Embed 建構 ────────────────────────────────────────────────────────────────
def _build_month_embed(zone: str, et_year: int, et_month: int) -> discord.Embed:
    """
    建立 ET 月曆 embed。
    每行格式（backtick 使日期欄等寬）：
      `MM/DD(月相)` emoji天氣1 · emoji天氣2 · emoji天氣3
    三欄分別是 ET 00:00 / 08:00 / 16:00 起的天氣時段。
    """
    month_start = _et_month_start_unix(et_year, et_month)

    embed = discord.Embed(
        title=f"📅 {zone} 天氣月曆",
        description=(
            f"**ET 第 {et_year} 年　第 {et_month} 月**"
            f"　｜　{_ZONE_TO_REGION.get(zone, '?')} / {ZONE_DATA[zone]['en']}\n"
            f"-# 三欄依序：ET `00:00` · `08:00` · `16:00` 起的天氣"
        ),
        color=discord.Color.teal(),
    )

    # 4 個 field，每個 8 ET 天（8 行 × ~35 字 ≈ 280 字，遠低於 1024 上限）
    now = int(time.time())
    for chunk in range(4):
        lines = []
        for d in range(chunk * 8, (chunk + 1) * 8):
            day_start = month_start + d * _ET_DAY_REAL
            et_day    = d + 1
            moon      = _MOON_PHASES[d // 4]
            parts_list = []
            for i in range(3):
                ws = day_start + i * WEATHER_DURATION
                w  = _get_weather(zone, ws)
                entry = f"{WEATHER_EMOJI.get(w, '🌡️')}{w}"
                if ws + WEATHER_DURATION <= now:
                    entry = f"~~{entry}~~"
                parts_list.append(entry)
            parts = " · ".join(parts_list)
            lines.append(f"`{et_month:02d}/{et_day:02d}({moon})` {parts} <t:{day_start}:t>")

        embed.add_field(
            name=f"第 {chunk * 8 + 1} ～ {(chunk + 1) * 8} 天",
            value="\n".join(lines),
            inline=False,
        )

    return embed


# ── View（上一個月 / 下一個月）────────────────────────────────────────────────
class WeatherMonthView(discord.ui.View):
    def __init__(self, zone: str, et_year: int, et_month: int):
        super().__init__(timeout=300)
        self.zone     = zone
        self.et_year  = et_year
        self.et_month = et_month

    @discord.ui.button(label="◀ 上一個月", style=discord.ButtonStyle.secondary)
    async def prev_month(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.et_year, self.et_month = _adj_et_month(self.et_year, self.et_month, -1)
        await interaction.response.edit_message(
            embed=_build_month_embed(self.zone, self.et_year, self.et_month),
            view=self,
        )

    @discord.ui.button(label="▶ 下一個月", style=discord.ButtonStyle.secondary)
    async def next_month(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.et_year, self.et_month = _adj_et_month(self.et_year, self.et_month, +1)
        await interaction.response.edit_message(
            embed=_build_month_embed(self.zone, self.et_year, self.et_month),
            view=self,
        )


# ── Discord 指令 ──────────────────────────────────────────────────────────────
class WeatherCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="weather", description="查詢 FFXIV 地圖 ET 天氣月曆（個人顯示）")
    @app_commands.describe(
        region="遊戲區域（輸入關鍵字可篩選）",
        zone="地圖名稱（輸入關鍵字可篩選）",
    )
    async def weather(
        self,
        interaction: discord.Interaction,
        region: str,
        zone: str,
    ):
        if zone not in ZONE_DATA:
            await interaction.response.send_message(
                f"❌ 找不到地圖「{zone}」，請使用自動完成選項。", ephemeral=True
            )
            return

        et_year, et_month = _unix_to_et_ym(int(time.time()))
        embed = _build_month_embed(zone, et_year, et_month)
        view  = WeatherMonthView(zone, et_year, et_month)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @weather.autocomplete("region")
    async def region_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=r, value=r)
            for r in REGION_DATA
            if current.lower() in r.lower()
        ][:25]

    @weather.autocomplete("zone")
    async def zone_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        region_input = interaction.namespace.region or ""
        if region_input in REGION_DATA:
            candidates = REGION_DATA[region_input]
        else:
            matched_regions = [r for r in REGION_DATA if region_input.lower() in r.lower()]
            candidates = (
                [z for r in matched_regions for z in REGION_DATA[r]]
                if matched_regions else list(ZONE_DATA.keys())
            )
        return [
            app_commands.Choice(name=z, value=z)
            for z in candidates
            if current.lower() in z.lower()
        ][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(WeatherCog(bot))
