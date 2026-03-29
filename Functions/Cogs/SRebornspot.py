from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, Modal, Select, TextInput, View

# ── 路徑設定 ──────────────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", ".."))
_FUNC_DIR = os.path.normpath(os.path.join(_THIS_DIR, ".."))

# 讓 MapGenerator 可以被 import
if _FUNC_DIR not in sys.path:
    sys.path.insert(0, _FUNC_DIR)

from MapGenerator import generate_map  # noqa: E402
from BasicFunction import is_allowed  # noqa: E402

_SRANK_DATA_FILE = os.path.normpath(
    os.path.join(_THIS_DIR, "..", "..", "Data", "srank_data.json")
)
_STATE_FILE = os.path.normpath(
    os.path.join(_ROOT_DIR, "Config", "srank_state.json")
)
_HISTORY_LIMIT = 5
WORLD_NAMES = ["伊弗利特", "迦樓羅", "利維坦", "鳳凰", "奧汀", "巴哈姆特", "泰坦"]

# ── 資料載入 ──────────────────────────────────────────────────────────────────
with open(_SRANK_DATA_FILE, "r", encoding="utf-8") as _f:
    SRANK_DATA: dict = json.load(_f)

# 所有地圖名稱（全版本合併，去重保序）
ALL_MAPS: list[str] = []
for _ver_data in SRANK_DATA.values():
    for _name in _ver_data:
        if _name not in ALL_MAPS:
            ALL_MAPS.append(_name)

# 英文地圖名稱反查：{ "urqopacha": ("奧闊帕恰山", "7.0"), ... }
_EN_TO_MAP: dict[str, tuple[str, str]] = {}
for _ev, _emaps in SRANK_DATA.items():
    for _ezh, _einfo in _emaps.items():
        _EN_TO_MAP[_einfo["mapNameEn"].lower().strip()] = (_ezh, _ev)


# ── 狀態持久化 ────────────────────────────────────────────────────────────────
def _load_state() -> dict:
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _get_map_state(map_name: str, server: str) -> dict:
    state = _load_state()
    if server not in state:
        state[server] = {}
    if map_name not in state[server]:
        state[server][map_name] = {"cleared": [], "history": []}
        _save_state(state)
    return state[server][map_name]


def _set_cleared(map_name: str, cleared: list[str], server: str) -> None:
    state = _load_state()
    if server not in state:
        state[server] = {}
    if map_name not in state[server]:
        state[server][map_name] = {"cleared": [], "history": []}
    state[server][map_name]["cleared"] = cleared
    _save_state(state)


def _add_history(map_name: str, point: str, user_id: int, user_name: str, server: str) -> None:
    state = _load_state()
    if server not in state:
        state[server] = {}
    if map_name not in state[server]:
        state[server][map_name] = {"cleared": [], "history": []}
    history = state[server][map_name].setdefault("history", [])
    history.append({
        "point": point,
        "user_id": user_id,
        "user_name": user_name,
        "ts": time.time(),
    })
    # 只保留最近 N 筆
    state[server][map_name]["history"] = history[-_HISTORY_LIMIT:]
    _save_state(state)


def _clear_map(map_name: str, server: str) -> None:
    state = _load_state()
    if server not in state:
        state[server] = {}
    if map_name not in state[server]:
        state[server][map_name] = {"cleared": [], "history": []}
    state[server][map_name]["cleared"] = []
    # history 保留，不清除
    _save_state(state)


# ── 批次貼入解析 ──────────────────────────────────────────────────────────────
_BATCH_RE = re.compile(r"@[^A-Za-z]*([A-Za-z][^(]+?)\s*\(\s*([\d.]+)\s*,\s*([\d.]+)\s*\)")


def _find_closest_label(map_data: dict, x: float, y: float, tolerance: float = 1.0) -> str | None:
    best_label, best_dist = None, float("inf")
    for pt in map_data["points"]:
        dist = ((pt["x"] - x) ** 2 + (pt["y"] - y) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best_label = pt["label"]
    return best_label if best_dist <= tolerance else None


def _parse_and_apply_batch(
    text: str, server: str, user_id: int, user_name: str
) -> tuple[list[str], list[str]]:
    """解析批次貼入文字並排除對應點位。回傳 (成功清單, 失敗清單)。"""
    success_lines: list[str] = []
    fail_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _BATCH_RE.search(line)
        if not m:
            fail_lines.append(f"`{line[:50]}` — 格式錯誤")
            continue

        map_en_key = m.group(1).strip().lower()
        x = float(m.group(2))
        y = float(m.group(3))

        lookup = _EN_TO_MAP.get(map_en_key)
        if lookup is None:
            fail_lines.append(f"`{m.group(1).strip()}` — 找不到對應地圖")
            continue

        zh_name, ver = lookup
        map_data = SRANK_DATA[ver][zh_name]
        label = _find_closest_label(map_data, x, y)
        if label is None:
            fail_lines.append(f"`{m.group(1).strip()} ({x}, {y})` — 座標無匹配點位")
            continue

        map_state = _get_map_state(zh_name, server)
        cleared = map_state.get("cleared", [])
        if label not in cleared:
            cleared.append(label)
            _set_cleared(zh_name, cleared, server)
            _add_history(zh_name, label, user_id, user_name, server)
            success_lines.append(f"✅ **{zh_name}** {label}　`({x}, {y})`")
        else:
            success_lines.append(f"⬜ **{zh_name}** {label}　`({x}, {y})` 已排除")

    return success_lines, fail_lines


# ── Embed 建構 ────────────────────────────────────────────────────────────────
def _get_map_data(map_name: str) -> tuple[str, dict] | tuple[None, None]:
    """找出地圖所屬版本並回傳 (version, map_data)。"""
    for ver, maps in SRANK_DATA.items():
        if map_name in maps:
            return ver, maps[map_name]
    return None, None


def build_srank_embed(map_name: str, map_data: dict, version: str, map_state: dict, server: str) -> discord.Embed:
    cleared = map_state.get("cleared", [])
    history = map_state.get("history", [])

    embed = discord.Embed(
        title=f"{map_name}　S Rank 點位追蹤",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="S Rank 目標",
        value=f"**{map_data['srank']['name']}**",
        inline=False,
    )

    all_labels = [pt["label"] for pt in map_data["points"]]

    if history:
        hist_lines = []
        for h in reversed(history):
            ts = datetime.datetime.fromtimestamp(h["ts"]).strftime("%m/%d %H:%M")
            hist_lines.append(f"`{h['point']}`　<@{h['user_id']}>　{ts}")
        embed.add_field(
            name=f"最近 {_HISTORY_LIMIT} 筆排除紀錄",
            value="\n".join(hist_lines),
            inline=False,
        )

    update_time = datetime.datetime.now().strftime("%H:%M:%S")
    embed.set_footer(text=f"版本 {version}　｜　伺服器：{server}　｜　上次更新：{update_time}")
    return embed


# ── UI View ───────────────────────────────────────────────────────────────────
class SRankView(View):
    def __init__(self, map_name: str, version: str, server: str):
        super().__init__(timeout=None)
        self.map_name = map_name
        self.version = version
        self.server = server
        self._rebuild_select()

    def _rebuild_select(self):
        # 移除舊的 select（若有）
        to_remove = [item for item in self.children if isinstance(item, Select)]
        for item in to_remove:
            self.remove_item(item)

        _, map_data = _get_map_data(self.map_name)
        map_state = _get_map_state(self.map_name, self.server)
        cleared = map_state.get("cleared", [])
        all_labels = [pt["label"] for pt in map_data["points"]]
        remaining = [lb for lb in all_labels if lb not in cleared]

        select = ReportSelect(self.map_name, remaining)
        self.add_item(select)

    async def refresh_embed_only(self, interaction: discord.Interaction):
        """只更新 embed 與選單，不重生地圖圖片（速度快）。"""
        await interaction.response.defer()
        _, map_data = _get_map_data(self.map_name)
        map_state = _get_map_state(self.map_name, self.server)

        embed = build_srank_embed(self.map_name, map_data, self.version, map_state, self.server)
        self._rebuild_select()

        await interaction.edit_original_response(embed=embed, view=self)

    async def refresh_message(self, interaction: discord.Interaction):
        """重新生成圖片和 embed 並更新訊息。"""
        await interaction.response.defer()
        _, map_data = _get_map_data(self.map_name)
        map_state = _get_map_state(self.map_name, self.server)
        cleared = set(map_state.get("cleared", []))

        embed = build_srank_embed(self.map_name, map_data, self.version, map_state, self.server)
        self._rebuild_select()

        map_file = generate_map(map_data, cleared)
        embed.set_image(url="attachment://map.png")

        await interaction.edit_original_response(
            embed=embed,
            attachments=[map_file],
            view=self,
        )


class ReportSelect(Select):
    def __init__(self, map_name: str, remaining: list[str]):
        self.map_name = map_name
        if remaining:
            options = [
                discord.SelectOption(label=f"點位 {lb}", value=lb)
                for lb in remaining
            ]
            placeholder = "回報已排除的點位"
            disabled = False
        else:
            options = [discord.SelectOption(label="—", value="none")]
            placeholder = "所有點位已排除"
            disabled = True

        super().__init__(
            placeholder=placeholder,
            options=options,
            custom_id=f"srank_report_{map_name}",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.defer()
            return

        label = self.values[0]
        view: SRankView = self.view
        map_state = _get_map_state(self.map_name, view.server)
        cleared = map_state.get("cleared", [])

        if label not in cleared:
            cleared.append(label)
            _set_cleared(self.map_name, cleared, view.server)
            _add_history(
                self.map_name,
                label,
                interaction.user.id,
                interaction.user.display_name,
                view.server,
            )

        # 更新 embed 與選單（不重生地圖）
        await view.refresh_embed_only(interaction)


class RefreshSRankButton(Button):
    def __init__(self, map_name: str):
        super().__init__(
            label="🔄 刷新地圖",
            style=discord.ButtonStyle.secondary,
            custom_id=f"srank_refresh_{map_name}",
        )
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction):
        view: SRankView = self.view
        await view.refresh_message(interaction)


class ClearSRankButton(Button):
    def __init__(self, map_name: str):
        super().__init__(
            label="🗑️ 清空排除紀錄",
            style=discord.ButtonStyle.danger,
            custom_id=f"srank_clear_{map_name}",
        )
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction):
        view: SRankView = self.view
        _clear_map(self.map_name, view.server)
        await view.refresh_embed_only(interaction)


class BatchInputModal(Modal, title="📋 批次貼入 S Rank 點位"):
    spots: TextInput = TextInput(
        label="支援「繁中狩獵車」與「Turtal scouter」匯出",
        style=discord.TextStyle.paragraph,
        placeholder="Queen hawk @ Urqopacha ( 18.8 , 14.0 )\nNechuciho @ Urqopacha ( 21.6 , 20.4 )",
        required=True,
        max_length=4000,
    )

    def __init__(self, server: str):
        super().__init__()
        self.server = server

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        success, fail = _parse_and_apply_batch(
            self.spots.value,
            self.server,
            interaction.user.id,
            interaction.user.display_name,
        )
        lines: list[str] = [f"**伺服器：{self.server}**"]
        if success:
            lines.append("\n**已排除點位：**")
            lines.extend(success)
        if fail:
            lines.append("\n**無法解析：**")
            lines.extend(fail)
        if not success and not fail:
            lines.append("（無有效資料）")
        await interaction.followup.send("\n".join(lines), ephemeral=True)


class BatchInputButton(Button):
    def __init__(self, map_name: str):
        super().__init__(
            label="📋 批次貼入",
            style=discord.ButtonStyle.primary,
            custom_id=f"srank_batch_{map_name}",
        )

    async def callback(self, interaction: discord.Interaction):
        view: SRankView = self.view
        await interaction.response.send_modal(BatchInputModal(server=view.server))


class ShareToChannelButton(Button):
    def __init__(self, map_name: str):
        super().__init__(
            label="📢 分享到頻道",
            style=discord.ButtonStyle.success,
            custom_id=f"srank_share_{map_name}",
        )
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction):
        view: SRankView = self.view
        await interaction.response.defer(ephemeral=True)

        if interaction.channel is None:
            await interaction.followup.send("❌ 無法取得頻道，此功能僅限伺服器頻道使用。", ephemeral=True)
            return

        _, map_data = _get_map_data(self.map_name)
        map_state = _get_map_state(self.map_name, view.server)
        cleared = set(map_state.get("cleared", []))

        embed = build_srank_embed(self.map_name, map_data, view.version, map_state, view.server)
        map_file = generate_map(map_data, cleared)
        embed.set_image(url="attachment://map.png")

        try:
            await interaction.channel.send(embed=embed, file=map_file)
            print(f"[ShareToChannel] user={interaction.user} ({interaction.user.id}) | map={self.map_name} | server={view.server} | channel={getattr(interaction.channel, 'name', 'N/A')}")
            await interaction.followup.send("✅ 已分享到頻道！", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 機器人沒有在此頻道發送訊息的權限。", ephemeral=True)


def build_srank_view(map_name: str, version: str, server: str) -> SRankView:
    view = SRankView(map_name, version, server)
    view.add_item(RefreshSRankButton(map_name))
    view.add_item(ClearSRankButton(map_name))
    view.add_item(BatchInputButton(map_name))
    view.add_item(ShareToChannelButton(map_name))
    return view


# ── Cog ───────────────────────────────────────────────────────────────────────
class SRebornSpot(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    @app_commands.command(
        name="srankmap",
        description="顯示指定地圖的 S Rank 點位追蹤",
    )
    @is_allowed()
    @app_commands.describe(
        version="遊戲版本",
        server="遊戲伺服器",
        map_name="地圖名稱",
    )
    @app_commands.choices(
        version=[
            app_commands.Choice(name="7.0（黃金遺産）", value="7.0"),
            app_commands.Choice(name="6.0（曉月之終途）", value="6.0"),
        ],
        server=[app_commands.Choice(name=w, value=w) for w in WORLD_NAMES],
    )
    async def srankmap(
        self,
        interaction: discord.Interaction,
        version: app_commands.Choice[str],
        server: app_commands.Choice[str],
        map_name: str,
    ):
        await interaction.response.defer(ephemeral=True)
        print(f"[/srankmap] user={interaction.user} ({interaction.user.id}) | version={version.value} | server={server.value} | map={map_name} | guild={getattr(interaction.guild, 'name', 'DM')} | channel={getattr(interaction.channel, 'name', 'N/A')}")

        ver = version.value
        ser = server.value
        if ver not in SRANK_DATA or map_name not in SRANK_DATA[ver]:
            await interaction.followup.send(
                f"❌ 找不到地圖「{map_name}」（版本 {ver}），請確認名稱是否正確。",
                ephemeral=True,
            )
            return

        map_data = SRANK_DATA[ver][map_name]
        map_state = _get_map_state(map_name, ser)
        cleared = set(map_state.get("cleared", []))

        embed = build_srank_embed(map_name, map_data, ver, map_state, ser)
        map_file = generate_map(map_data, cleared)
        embed.set_image(url="attachment://map.png")

        view = build_srank_view(map_name, ver, ser)
        await interaction.followup.send(embed=embed, file=map_file, view=view, ephemeral=True)

    @srankmap.autocomplete("map_name")
    async def map_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        # 取得已選版本（可能尚未填）
        ver = interaction.namespace.version or "7.0"
        candidates = list(SRANK_DATA.get(ver, {}).keys())
        filtered = [m for m in candidates if current.lower() in m.lower()]
        return [app_commands.Choice(name=m, value=m) for m in filtered[:25]]

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error) if str(error) else "❌ 你沒有權限使用此指令。"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        else:
            raise error


async def setup(client: commands.Bot):
    await client.add_cog(SRebornSpot(client))
