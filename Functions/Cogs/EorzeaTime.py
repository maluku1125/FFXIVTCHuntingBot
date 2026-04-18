import time
import discord
from discord import app_commands
from discord.ext import commands

# 1 ET day = 70 real minutes → ratio = 144/7
_EORZEA_RATIO = 144 / 7
_ET_MONTH_SECS = 2764800   # ET 秒 / 月
_ET_YEAR_SECS  = 33177600  # ET 秒 / 年


def _parse_et(ts=None):
    real_ms = (ts if ts is not None else time.time()) * 1000
    t = int(real_ms * _EORZEA_RATIO // 1000)
    return {
        "year":   t // _ET_YEAR_SECS,
        "month":  (t // _ET_MONTH_SECS) % 12 + 1,
        "day":    (t // 86400) % 32 + 1,
        "hour":   (t // 3600) % 24,
        "minute": (t // 60) % 60,
    }


def _et_to_unix(year, month, day):
    et_s = year * _ET_YEAR_SECS + (month - 1) * _ET_MONTH_SECS + (day - 1) * 86400
    return int(et_s * 7 / 144)


def _adj_month(year, month, delta):
    total = (year * 12 + (month - 1)) + delta
    return total // 12, total % 12 + 1


def _build_current_embed():
    et = _parse_et()
    embed = discord.Embed(
        title="⏰ 艾歐澤亞時間 (Eorzea Time)",
        description=(
            f"**{et['year']} 年　第 {et['month']} 月　第 {et['day']} 日**\n"
            f"**{et['hour']:02d}:{et['minute']:02d}**"
        ),
        color=discord.Color.gold(),
    )
    return embed


def _parse_et_hhmm(raw: str) -> tuple[int, int] | None:
    """將使用者輸入（1~4 位數字）解析為 (hour, minute)，無效時回傳 None。"""
    s = raw.strip().replace(":", "")
    if not s.isdigit():
        return None
    if len(s) <= 2:
        h, m = int(s), 0
    elif len(s) == 3:
        h, m = int(s[0]), int(s[1:])
    elif len(s) == 4:
        h, m = int(s[:2]), int(s[2:])
    else:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def _next_et_hhmm_unix(hour: int, minute: int) -> int:
    """回傳下一次 ET HH:MM 對應的現實 Unix 時間戳（秒）。"""
    _ET_DAY_ET_S = 86400          # 1 ET 日 = 86400 ET 秒
    now_real     = time.time()
    now_et_s     = int(now_real * _EORZEA_RATIO)   # 目前 ET 秒
    et_day_start = now_et_s - (now_et_s % _ET_DAY_ET_S)
    target_off   = hour * 3600 + minute * 60        # 目標在 ET 日內的偏移（ET 秒）
    target_et_s  = et_day_start + target_off
    if target_et_s <= now_et_s:                     # 今天這個時間已過，取下一 ET 日
        target_et_s += _ET_DAY_ET_S
    return int(target_et_s * 7 / 144)              # ET 秒 → 現實秒


def _build_countdown_embed(hour: int, minute: int, unix_ts: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"⏱️ ET {hour:02d}:{minute:02d} 倒數",
        color=discord.Color.gold(),
    )
    embed.add_field(name="現實時間", value=f"<t:{unix_ts}:f>", inline=False)
    embed.add_field(name="距離現在", value=f"<t:{unix_ts}:R>", inline=False)
    embed.set_footer(text="每 ET 日 = 70 現實分鐘")
    return embed


def _build_month_embed(year, month):
    lines = []
    for day in range(1, 33):
        unix_ts = _et_to_unix(year, month, day)
        lines.append(f"`ET {month:02d}/{day:02d}`　<t:{unix_ts}:f>")
    embed = discord.Embed(
        title=f"📅 ET {year} 年　第 {month} 月",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    embed.set_footer(text="時間以本地時區顯示 | 每 ET 日 = 70 現實分鐘")
    return embed


class ETCurrentView(discord.ui.View):
    """初始畫面：只有一個「切換月曆」按鈕。"""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="📅 切換月曆", style=discord.ButtonStyle.primary)
    async def to_calendar(self, interaction: discord.Interaction, _: discord.ui.Button):
        et = _parse_et()
        await interaction.response.edit_message(
            embed=_build_month_embed(et["year"], et["month"]),
            view=ETMonthView(et["year"], et["month"]),
        )


class ETMonthView(discord.ui.View):
    def __init__(self, year, month):
        super().__init__(timeout=300)
        self.year  = year
        self.month = month

    @discord.ui.button(label="◀ 上一個月", style=discord.ButtonStyle.secondary)
    async def prev_month(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.year, self.month = _adj_month(self.year, self.month, -1)
        await interaction.response.edit_message(
            embed=_build_month_embed(self.year, self.month), view=self
        )

    @discord.ui.button(label="▶ 下一個月", style=discord.ButtonStyle.secondary)
    async def next_month(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.year, self.month = _adj_month(self.year, self.month, +1)
        await interaction.response.edit_message(
            embed=_build_month_embed(self.year, self.month), view=self
        )


class EorzeaTime(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="et", description="顯示當前艾歐澤亞時間；輸入 ET 時間（如 2100）可查詢倒數")
    @app_commands.describe(et_time="ET 時間（格式：HHMM，例如 2100 代表 ET 21:00）")
    async def et(self, interaction: discord.Interaction, et_time: str | None = None):
        if et_time is not None:
            parsed = _parse_et_hhmm(et_time)
            if parsed is None:
                await interaction.response.send_message(
                    "❌ 無效的 ET 時間格式，請輸入 1~4 位數字，例如 `2100` 或 `930`。",
                    ephemeral=True,
                )
                return
            h, m = parsed
            unix_ts = _next_et_hhmm_unix(h, m)
            await interaction.response.send_message(
                embed=_build_countdown_embed(h, m, unix_ts), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=_build_current_embed(), view=ETCurrentView(), ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(EorzeaTime(bot))

