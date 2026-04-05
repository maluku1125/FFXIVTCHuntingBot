import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
import time
import json
import os
import sys
import datetime

# ── 共用權限檢查 ──────────────────────────────────────────────────────────────
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
_FUNC_DIR  = os.path.normpath(os.path.join(_THIS_DIR, ".."))
if _FUNC_DIR not in sys.path:
    sys.path.insert(0, _FUNC_DIR)
from BasicFunction import is_allowed  # noqa: E402

# ── 常數 ──────────────────────────────────────────────────────────────────────
RESPAWN_SECONDS  = 6 * 3600   # 6h 後進入存活
COOLDOWN_HOURS   = 4           # 0~4h：冷卻
REGEN_HOURS      = 6           # 4~6h：再生
ALIVE_MAX_HOURS  = 30          # 超過 30h：視為重置回冷卻

WORLD_NAMES = ["伊弗利特", "迦樓羅", "利維坦", "鳳凰", "奧汀", "巴哈姆特", "泰坦"]

_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "..", "Config", "hunt_state.json"))

_hunt_state:  dict[int, list] = {}
_scout_state: dict[int, list] = {}


# ── 持久化 ────────────────────────────────────────────────────────────────────

def _load_all_persisted() -> dict:
    """讀取全部 guild 的狀態，格式：{ "guild_id": { channel_id, message_id, ... } }"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 舊格式相容：若 root 有 channel_id 表示舊版單一面板，忽略
            if "channel_id" in data:
                return {}
            return data
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _load_persisted(guild_id: int) -> dict:
    return _load_all_persisted().get(str(guild_id), {})


def _save_persisted(guild_id: int, channel_id: int, message_id: int) -> None:
    data = _load_all_persisted()
    data[str(guild_id)] = {
        "channel_id": channel_id,
        "message_id": message_id,
        "kill_times":  _hunt_state.get(message_id,  [None] * len(WORLD_NAMES)),
        "scout_users": _scout_state.get(message_id, [None] * len(WORLD_NAMES)),
    }
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 外部呼叫：立即刷新面板 ───────────────────────────────────────────────────────

async def refresh_panel(bot: commands.Bot, guild_id: int) -> None:
    """重新從 JSON 讀取並立即刷新指定 guild 的面板 embed。"""
    state = _load_persisted(guild_id)
    channel_id = state.get("channel_id")
    message_id = state.get("message_id")
    if not channel_id or not message_id:
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden):
        return
    kill_times  = state.get("kill_times",  [None] * len(WORLD_NAMES))
    scout_users = state.get("scout_users", [None] * len(WORLD_NAMES))
    _hunt_state[message_id]  = kill_times
    _scout_state[message_id] = scout_users
    embed = build_embed(kill_times, scout_users)
    view  = build_view(kill_times, scout_users)
    try:
        await message.edit(embed=embed, view=view)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        print(f"[refresh_panel] edit failed for guild {guild_id}: {e!r}")


# ── 狀態判斷 ──────────────────────────────────────────────────────────────────

def get_status(kill_ts) -> str:
    """依討伐後經過時間回傳狀態字串。"""
    if kill_ts is None:
        return "未知"
    h = (time.time() - kill_ts) / 3600
    if h > ALIVE_MAX_HOURS:
        return "未知"      # 超過 30h，視為重置
    if h >= REGEN_HOURS:
        return "存活"      # 6~30h
    if h >= COOLDOWN_HOURS:
        return "再生"      # 4~6h
    return "冷卻"          # 0~4h


def get_button_appearance(kill_ts) -> tuple[discord.ButtonStyle, bool]:
    """依狀態回傳 (ButtonStyle, disabled)。"""
    status = get_status(kill_ts)
    if status == "存活":
        return discord.ButtonStyle.success,   False  # 綠，可按
    if status == "再生":
        return discord.ButtonStyle.secondary, False  # 灰，可按
    if status == "冷卻":
        return discord.ButtonStyle.danger,    True   # 紅，禁用
    return discord.ButtonStyle.primary, False        # 藍（未知），可按


def make_progress_bar(percent: float) -> str:
    filled = max(0, min(10, int(percent // 10)))
    return "▓" * filled + "░" * (10 - filled)


# ── Embed 建構 ────────────────────────────────────────────────────────────────

def build_embed(kill_times: list, scout_users: list) -> discord.Embed:
    embed = discord.Embed(title="7A狩獵刷新時間", color=discord.Color.blurple())
    now = time.time()

    # 排序：存活(3) > 未知(2) > 再生(1) > 冷卻(0)；同優先以經過時間降序
    def sort_key(i):
        kt = kill_times[i]
        if kt is None:
            return (2, 0.0)
        h = (now - kt) / 3600
        if h > ALIVE_MAX_HOURS:
            return (2, 0.0)
        if h >= REGEN_HOURS:
            return (3, h)
        if h >= COOLDOWN_HOURS:
            return (1, h)
        return (0, h)

    for i in sorted(range(len(WORLD_NAMES)), key=sort_key, reverse=True):
        name     = WORLD_NAMES[i]
        kill_ts  = kill_times[i]
        scout_uid = scout_users[i] if scout_users else None
        status   = get_status(kill_ts)

        elapsed = (now - kill_ts) if kill_ts is not None else None

        # 進度條：0→RESPAWN_SECONDS 為 0~100%，之後維持 100%
        if elapsed is None or elapsed > ALIVE_MAX_HOURS * 3600:
            pct = 0.0
        elif elapsed >= RESPAWN_SECONDS:
            pct = 100.0
        else:
            pct = elapsed / RESPAWN_SECONDS * 100

        bar = make_progress_bar(pct)

        if elapsed is None or elapsed > ALIVE_MAX_HOURS * 3600:
            time_str = "-"
        else:
            dt = datetime.datetime.fromtimestamp(kill_ts)
            time_str = f"{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}"

        scout_line = f"\n偵查中：<@{scout_uid}>" if scout_uid else ""
        value = f"`{bar}` {pct:.0f}% [{status}] 前次討伐 : {time_str}{scout_line}"
        embed.add_field(name=f"**{name}**", value=value, inline=False)

    update_time = datetime.datetime.now().strftime("%H:%M:%S")
    embed.set_footer(text=f"上次更新：{update_time} ｜ 🟢存活 ｜ ⚪再生 ｜ 🔴冷卻 ")
    return embed


# ── UI 元件 ───────────────────────────────────────────────────────────────────

class WorldButton(Button):
    def __init__(
        self,
        world_index: int,
        world_name:  str,
        style:    discord.ButtonStyle = discord.ButtonStyle.primary,
        disabled: bool = False,
    ):
        super().__init__(
            label=world_name,
            style=style,
            custom_id=f"hunt_world_{world_index}",
            disabled=disabled,
        )
        self.world_index = world_index

    async def callback(self, interaction: discord.Interaction):
        msg_id   = interaction.message.id
        guild_id = interaction.guild_id
        if msg_id not in _hunt_state or msg_id not in _scout_state:
            saved = _load_persisted(guild_id)
            if msg_id not in _hunt_state:
                _hunt_state[msg_id] = saved.get("kill_times",  [None] * len(WORLD_NAMES))
            if msg_id not in _scout_state:
                _scout_state[msg_id] = saved.get("scout_users", [None] * len(WORLD_NAMES))
        _hunt_state[msg_id][self.world_index] = time.time()
        _scout_state[msg_id][self.world_index] = None  # 討伐後清除偵查狀態

        _save_persisted(guild_id, interaction.channel_id, msg_id)

        embed = build_embed(_hunt_state[msg_id], _scout_state[msg_id])
        view  = build_view(_hunt_state[msg_id],  _scout_state[msg_id])
        await interaction.response.edit_message(embed=embed, view=view)


class RefreshButton(Button):
    def __init__(self):
        super().__init__(
            label="🔄 刷新",
            style=discord.ButtonStyle.secondary,
            custom_id="hunt_refresh",
        )

    async def callback(self, interaction: discord.Interaction):
        msg_id     = interaction.message.id
        kill_times  = _hunt_state.get(msg_id)
        scout_users = _scout_state.get(msg_id)
        if kill_times is None or scout_users is None:
            saved = _load_persisted(interaction.guild_id)
            if kill_times is None:
                kill_times = saved.get("kill_times",  [None] * len(WORLD_NAMES))
                _hunt_state[msg_id] = kill_times
            if scout_users is None:
                scout_users = saved.get("scout_users", [None] * len(WORLD_NAMES))
                _scout_state[msg_id] = scout_users
        embed = build_embed(kill_times, scout_users)
        view  = build_view(kill_times,  scout_users)
        await interaction.response.edit_message(embed=embed, view=view)


class ScoutSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, value=str(i))
            for i, name in enumerate(WORLD_NAMES)
        ]
        super().__init__(
            placeholder="選擇偵查中的伺服器（再選一次可取消）",
            options=options,
            custom_id="scout_select",
        )

    async def callback(self, interaction: discord.Interaction):
        msg_id     = interaction.message.id
        guild_id   = interaction.guild_id
        boss_index = int(self.values[0])
        if msg_id not in _hunt_state or msg_id not in _scout_state:
            saved = _load_persisted(guild_id)
            if msg_id not in _hunt_state:
                _hunt_state[msg_id] = saved.get("kill_times",  [None] * len(WORLD_NAMES))
            if msg_id not in _scout_state:
                _scout_state[msg_id] = saved.get("scout_users", [None] * len(WORLD_NAMES))

        if _scout_state[msg_id][boss_index] == interaction.user.id:
            _scout_state[msg_id][boss_index] = None
        else:
            for j in range(len(WORLD_NAMES)):
                if _scout_state[msg_id][j] == interaction.user.id:
                    _scout_state[msg_id][j] = None
            _scout_state[msg_id][boss_index] = interaction.user.id

        _save_persisted(guild_id, interaction.channel_id, msg_id)

        embed = build_embed(_hunt_state[msg_id], _scout_state[msg_id])
        view  = build_view(_hunt_state[msg_id],  _scout_state[msg_id])
        await interaction.response.edit_message(embed=embed, view=view)


class HuntView(View):
    """持久化 View，Bot 重啟後仍可接收互動（按鈕預設全部啟用）。"""
    def __init__(self):
        super().__init__(timeout=None)
        for i, name in enumerate(WORLD_NAMES):
            self.add_item(WorldButton(world_index=i, world_name=name))
        self.add_item(ScoutSelect())
        self.add_item(RefreshButton())


def build_view(kill_times: list, scout_users: list = None) -> View:
    """依目前狀態動態設定按鈕樣式（綠/灰/紅/藍）。"""
    view = View(timeout=None)
    for i, name in enumerate(WORLD_NAMES):
        style, disabled = get_button_appearance(kill_times[i])
        view.add_item(WorldButton(world_index=i, world_name=name, style=style, disabled=disabled))
    view.add_item(ScoutSelect())
    view.add_item(RefreshButton())
    return view


# ── Cog ───────────────────────────────────────────────────────────────────────

class ATrainOverview(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        client.add_view(HuntView())   # 持久化 View 全域註冊
        self._restore_state()
        self.auto_refresh.start()

    def cog_unload(self):
        self.auto_refresh.cancel()

    def _restore_state(self):
        all_data = _load_all_persisted()
        for guild_id_str, state in all_data.items():
            if "message_id" in state:
                msg_id = state["message_id"]
                _hunt_state[msg_id]  = state.get("kill_times",  [None] * len(WORLD_NAMES))
                _scout_state[msg_id] = state.get("scout_users", [None] * len(WORLD_NAMES))

    @tasks.loop(minutes=5)
    async def auto_refresh(self):
        all_data = _load_all_persisted()
        for guild_id_str, state in all_data.items():
            channel_id = state.get("channel_id")
            message_id = state.get("message_id")
            if not channel_id or not message_id:
                continue

            channel = self.client.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.client.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
            try:
                message = await channel.fetch_message(message_id)
            except (discord.NotFound, discord.Forbidden):
                continue

            kill_times  = state.get("kill_times",  [None] * len(WORLD_NAMES))
            scout_users = state.get("scout_users", [None] * len(WORLD_NAMES))
            _hunt_state[message_id]  = kill_times
            _scout_state[message_id] = scout_users
            embed = build_embed(kill_times, scout_users)
            view  = build_view(kill_times,  scout_users)
            try:
                await message.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                print(f"[auto_refresh] edit failed for guild {guild_id_str}: {e!r}")
                continue

    @auto_refresh.error
    async def auto_refresh_error(self, error):
        print(f"[auto_refresh] task crashed: {error!r}")
        if not self.auto_refresh.is_running():
            self.auto_refresh.start()

    @auto_refresh.before_loop
    async def before_auto_refresh(self):
        await self.client.wait_until_ready()

    # ── /set7atimerchannel：建立面板 ──────────────────────────────────────────────────────

    @app_commands.command(name="set7atimerchannel", description="在此頻道建立狩獵追蹤面板（每個伺服器各一個）")
    @app_commands.guild_only()
    @is_allowed()
    async def set7atimerchannel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        print(f"[/set7atimerchannel] user={interaction.user} ({interaction.user.id}) | guild={getattr(interaction.guild, 'name', 'DM')} | channel={getattr(interaction.channel, 'name', 'N/A')}")

        guild_id       = interaction.guild_id
        state          = _load_persisted(guild_id)
        old_channel_id = state.get("channel_id")
        old_message_id = state.get("message_id")

        # 保留舊的討伐資料，跨頻道遷移時不清空
        if old_message_id:
            kill_times  = _hunt_state.pop(old_message_id, state.get("kill_times",  [None] * len(WORLD_NAMES)))
            scout_users = _scout_state.pop(old_message_id, state.get("scout_users", [None] * len(WORLD_NAMES)))
        else:
            kill_times  = [None] * len(WORLD_NAMES)
            scout_users = [None] * len(WORLD_NAMES)

        if old_channel_id and old_message_id:
            old_channel = self.client.get_channel(old_channel_id)
            if old_channel:
                try:
                    old_msg = await old_channel.fetch_message(old_message_id)
                    await old_msg.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

        embed = build_embed(kill_times, scout_users)
        view  = HuntView()
        msg   = await interaction.channel.send(embed=embed, view=view)

        _hunt_state[msg.id]  = kill_times
        _scout_state[msg.id] = scout_users
        _save_persisted(guild_id, interaction.channel_id, msg.id)

        await interaction.followup.send("✅ 狩獵追蹤面板已在此頻道建立！", ephemeral=True)

    # ── /reset7aworld：設定或清除單一世界討伐時間 ────────────────────────────

    @app_commands.command(name="reset7aworld", description="設定或清除指定伺服器的7A討伐時間記錄")
    @app_commands.describe(
        world="選擇要設定的伺服器",
        kill_time="討伐時間，格式 HH:MM 或 MM/DD HH:MM（留空則清除記錄）",
    )
    @app_commands.guild_only()
    @is_allowed()
    @app_commands.choices(world=[
        app_commands.Choice(name=name, value=str(i))
        for i, name in enumerate(WORLD_NAMES)
    ])
    async def reset7aworld(
        self,
        interaction: discord.Interaction,
        world: app_commands.Choice[str],
        kill_time: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        print(f"[/reset7aworld] user={interaction.user} ({interaction.user.id}) | world={world.name} | kill_time={kill_time!r} | guild={getattr(interaction.guild, 'name', 'DM')} | channel={getattr(interaction.channel, 'name', 'N/A')}")

        # ── 解析時間 ──────────────────────────────────────────────────────────
        new_ts: float | None = None
        if kill_time is not None:
            now_dt = datetime.datetime.now()
            parsed = None
            for fmt in ("%m/%d %H:%M", "%H:%M"):
                try:
                    parsed = datetime.datetime.strptime(kill_time.strip(), fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                await interaction.followup.send(
                    "❌ 時間格式錯誤，請使用 `HH:MM` 或 `MM/DD HH:MM`。", ephemeral=True
                )
                return
            # 補全年份（及月份/日期）
            if fmt == "%H:%M":
                parsed = parsed.replace(year=now_dt.year, month=now_dt.month, day=now_dt.day)
            else:
                parsed = parsed.replace(year=now_dt.year)
            # 若解析結果在未來，視為前一年
            if parsed > now_dt:
                parsed = parsed.replace(year=now_dt.year - 1)
            new_ts = parsed.timestamp()

        # ── 更新狀態 ──────────────────────────────────────────────────────────
        guild_id   = interaction.guild_id
        state      = _load_persisted(guild_id)
        message_id = state.get("message_id")
        channel_id = state.get("channel_id")
        if not message_id:
            await interaction.followup.send("❌ 此伺服器尚未建立狩獵面板，請先使用 `/set7atimerchannel`。", ephemeral=True)
            return

        world_index = int(world.value)
        if message_id not in _hunt_state:
            _hunt_state[message_id] = [None] * len(WORLD_NAMES)
        if message_id not in _scout_state:
            _scout_state[message_id] = [None] * len(WORLD_NAMES)

        _hunt_state[message_id][world_index] = new_ts
        _save_persisted(guild_id, channel_id, message_id)

        channel = self.client.get_channel(channel_id)
        if channel:
            try:
                message = await channel.fetch_message(message_id)
                embed = build_embed(_hunt_state[message_id], _scout_state[message_id])
                view  = build_view(_hunt_state[message_id],  _scout_state[message_id])
                await message.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden):
                pass

        if new_ts is None:
            reply = f"✅ 已清除 **{world.name}** 的討伐時間記錄。"
        else:
            dt_str = datetime.datetime.fromtimestamp(new_ts).strftime("%m/%d %H:%M")
            reply = f"✅ 已將 **{world.name}** 的討伐時間設定為 `{dt_str}`。"
        await interaction.followup.send(reply, ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error) if str(error) else "❌ 你沒有權限使用此指令。"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, discord.Forbidden):
            msg = "❌ 此指令只能在**伺服器頻道**中使用，無法在個人應用程式環境下執行。"
            print(f"[ERROR] Forbidden in {error.command.name}: {error.original}")
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        else:
            raise error


async def setup(client: commands.Bot):
    await client.add_cog(ATrainOverview(client))
