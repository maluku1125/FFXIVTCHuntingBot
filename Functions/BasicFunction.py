from __future__ import annotations

import configparser
import os

import discord
from discord import app_commands

_CONFIG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "Config", "FFXIVTC-Huntingbot_config.ini")
)


def _load_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(_CONFIG_PATH, encoding="utf-8")
    return cfg


def is_allowed(command: str = ""):
    """
    app_commands.check 裝飾器。
    允許 admin_id（擁有者）或持有 [command] 中對應項別名稱身分組的使用者。
    INI 設定：
        [bot]
        admin_id = 你的 Discord 用戶 ID
        [command]
        <指令名稱> = 身分組ID1,身分組ID2,...
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        cfg = _load_config()
        admin_id_str = cfg.get("bot", "admin_id", fallback="").strip()

        # admin 直接通過
        if admin_id_str and str(interaction.user.id) == admin_id_str:
            return True

        # 讀取該指令專屬的身分組清單
        cmd_key = command or (interaction.command.name if interaction.command else "")
        allowed_roles_str = cfg.get("command", cmd_key, fallback="").strip()

        if allowed_roles_str:
            allowed_ids   = {int(r.strip()) for r in allowed_roles_str.split(",") if r.strip().isdigit()}
            user_role_ids = {role.id for role in getattr(interaction.user, "roles", [])}
            if allowed_ids & user_role_ids:
                return True

        raise app_commands.CheckFailure("❌ 你沒有權限使用此指令。")

    return app_commands.check(predicate)
