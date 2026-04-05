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

    @app_commands.command(name="et", description="顯示當前艾歐澤亞時間，可切換月曆對照現實時間")
    async def et(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=_build_current_embed(), view=ETCurrentView(), ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(EorzeaTime(bot))

