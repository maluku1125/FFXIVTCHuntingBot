import configparser
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

# ── 常數 ──────────────────────────────────────────────────────────────────────
_THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "..", "Config", "FFXIVTC-Huntingbot_config.ini"))
_STATE_FILE  = os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "..", "Config", "hunt_state.json"))
_TZ_JST      = timezone(timedelta(hours=9))   # 遊戲時間 = JST/UTC+9 (含台灣 UTC+8，視訊息而定)
_TZ_TW       = timezone(timedelta(hours=8))   # 台灣時間 UTC+8

# ── 伺服器名稱與別名對照（index 需與 ATrainOverview.WORLD_NAMES 一致）───────────
WORLD_NAMES = ["伊弗利特", "迦樓羅", "利維坦", "鳳凰", "奧汀", "巴哈姆特", "泰坦"]

# 每個伺服器常見稱呼（可自行擴充）
WORLD_ALIASES: dict[int, list[str]] = {
    0: ["伊弗利特", "伊弗", "火神", "ifrit"],
    1: ["迦樓羅",  "garuda", "風神"],
    2: ["利維坦", "水神", "leviathan"],
    3: ["鳳凰", "火鳥", "phoenix"],
    4: ["奧汀", "odin"],
    5: ["巴哈姆特", "巴哈", "bahamut"],
    6: ["泰坦", "土神", "titan"],
}

# 結束關鍵字（hardcode，不放 ini）
_END_KEYWORDS = re.compile(r'結束時間|結束|done|DONE|到站|到站時間|end|END|完成|\bend\b|\bend\b', re.IGNORECASE)
_STRIKETHROUGH = re.compile(r'~~.+?~~', re.DOTALL)

# 時間 regex（依優先順序）
_RE_DISCORD_TS = re.compile(r'<t:(\d+)(?::[tTdDfFR])?>')
_RE_TST        = re.compile(r'(?i)TST\s*(\d{3,4})')
_RE_HHMM_COLON = re.compile(r'\b(\d{1,2}):(\d{2})(?!\d)')  # 尾部改用 (?!\d) 避免中文 \b 失效
_RE_HHMM_PLAIN = re.compile(r'\b(\d{3,4})\b')
_RE_AMPM       = re.compile(r'(上午|下午)\s*(\d{1,2}):(\d{2})')  # 12 小時制 → 24 小時制


def _normalize_ampm(content: str) -> str:
    """將「上午/下午 H:MM」轉為 24 小時制，以便後續 regex 正確解析。"""
    def _repl(m):
        ampm, h, mn = m.group(1), int(m.group(2)), m.group(3)
        if ampm == '下午' and h != 12:
            h += 12
        elif ampm == '上午' and h == 12:
            h = 0
        return f"{h:02d}:{mn}"
    return _RE_AMPM.sub(_repl, content)


def _to_unix_with_daywrap(h: int, mn: int) -> float:
    """HH:MM → Unix（UTC+8）。若合成結果比現在早超過 12 小時，自動加一天。"""
    now_tw    = datetime.now(_TZ_TW)
    candidate = now_tw.replace(hour=h, minute=mn, second=0, microsecond=0)
    if (now_tw - candidate).total_seconds() > 12 * 3600:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def _parse_times(content: str) -> list[tuple[int, int]]:
    """從訊息內容解析所有 (hour, minute)，去除重複位置。"""
    content = _normalize_ampm(content)
    results: list[tuple[int, int]] = []
    used_spans: list[tuple[int, int]] = []

    def _overlaps(span: tuple[int, int]) -> bool:
        for s, e in used_spans:
            if span[0] < e and span[1] > s:
                return True
        return False

    # 1. Discord timestamp <t:epoch>
    for m in _RE_DISCORD_TS.finditer(content):
        ts = int(m.group(1))
        dt = datetime.fromtimestamp(ts, tz=_TZ_TW)
        results.append((dt.hour, dt.minute))
        used_spans.append(m.span())

    # 2. TST1850 格式
    for m in _RE_TST.finditer(content):
        if _overlaps(m.span()):
            continue
        raw = m.group(1)
        if len(raw) <= 2:
            continue
        h, mn = divmod(int(raw), 100) if len(raw) == 4 else (int(raw[0]), int(raw[1:]))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            results.append((h, mn))
            used_spans.append(m.span())

    # 3. HH:MM
    for m in _RE_HHMM_COLON.finditer(content):
        if _overlaps(m.span()):
            continue
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            results.append((h, mn))
            used_spans.append(m.span())

    # 4. HHMM (純數字 3-4 位)
    for m in _RE_HHMM_PLAIN.finditer(content):
        if _overlaps(m.span()):
            continue
        raw = m.group(1)
        if len(raw) < 3:
            continue
        h = int(raw[:-2])
        mn = int(raw[-2:])
        if 0 <= h <= 23 and 0 <= mn <= 59:
            results.append((h, mn))
            used_spans.append(m.span())

    return results


def _match_world(content: str) -> int | None:
    """
    從訊息內容比對伺服器別名，回傳 WORLD_NAMES 的 index。
    若無匹配或同時匹配多個伺服器則回傳 None。
    """
    lower = content.lower()
    matched: set[int] = set()
    for idx, aliases in WORLD_ALIASES.items():
        for alias in aliases:
            if alias.lower() in lower:
                matched.add(idx)
                break  # 同一伺服器不需重複計
    if len(matched) == 1:
        return next(iter(matched))
    return None


def _has_end_signal(content: str) -> bool:
    """含刪除線 OR 結束關鍵字則回傳 True。"""
    return bool(_STRIKETHROUGH.search(content)) or bool(_END_KEYWORDS.search(content))


def _extract_end_time_unix(content: str) -> float | None:
    """
    從「結束時間」關鍵字之後提取結束時間，回傳 Unix timestamp。
    優先使用 Discord timestamp（精確含日期），其次 HH:MM（合成今日 UTC+8）。
    只解析「結束時間」之後的文字，避免誤取同行的開始時間。
    若無時間則回傳 None。
    """
    norm = _normalize_ampm(content)
    for line in norm.splitlines():
        idx = line.find('結束時間')
        if idx == -1:
            continue
        after = line[idx:]
        # 優先：Discord timestamp <t:epoch:...>
        m = _RE_DISCORD_TS.search(after)
        if m:
            return float(m.group(1))
        # 次要：HH:MM → 含跨日修正
        for m in _RE_HHMM_COLON.finditer(after):
            h, mn = int(m.group(1)), int(m.group(2))
            if 0 <= h <= 23 and 0 <= mn <= 59:
                return _to_unix_with_daywrap(h, mn)
    return None


def _extract_end_time_near_keyword(content: str) -> float | None:
    """
    掃描所有 END 關鍵字位置，取其前方（文字位置最近）的時間作為結束時間。
    同行或跨行均適用，並自動跨日修正。
    """
    norm = _normalize_ampm(content)

    # 收集所有時間與其文字位置，避免重複span
    time_items: list[tuple[int, int, object]] = []  # (start, end, value)
    # value: float（discord ts）或 (h, mn) tuple
    used: list[tuple[int, int]] = []

    def _overlaps(s: int, e: int) -> bool:
        for us, ue in used:
            if s < ue and e > us:
                return True
        return False

    for m in _RE_DISCORD_TS.finditer(norm):
        if not _overlaps(m.start(), m.end()):
            time_items.append((m.start(), m.end(), float(m.group(1))))
            used.append((m.start(), m.end()))

    for m in _RE_TST.finditer(norm):
        if _overlaps(m.start(), m.end()):
            continue
        raw = m.group(1)
        if len(raw) <= 2:
            continue
        h = int(raw[:-2]) if len(raw) == 4 else int(raw[0])
        mn = int(raw[-2:])
        if 0 <= h <= 23 and 0 <= mn <= 59:
            time_items.append((m.start(), m.end(), (h, mn)))
            used.append((m.start(), m.end()))

    for m in _RE_HHMM_COLON.finditer(norm):
        if _overlaps(m.start(), m.end()):
            continue
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            time_items.append((m.start(), m.end(), (h, mn)))
            used.append((m.start(), m.end()))

    if not time_items:
        return None

    # 對每個 END 關鍵字，取其前方位置最近的時間
    for end_m in _END_KEYWORDS.finditer(norm):
        end_pos = end_m.start()
        before  = [(s, e, v) for s, e, v in time_items if s < end_pos]
        if not before:
            continue
        _, _, value = max(before, key=lambda x: x[0])  # 位置最靠近 END 的
        if isinstance(value, float):
            return value
        h, mn = value
        return _to_unix_with_daywrap(h, mn)

    return None


def _resolve_end_time(after: discord.Message) -> float:
    """
    從訊息內容解析結束時間。
    1. 「結束時間」行直接提取（Discord ts 或 HH:MM）
    2. END 關鍵字前方最近的時間（同行或跨行均適用，含跨日修正）
    3. fallback：edited_at 或當前時間
    """
    ts = _extract_end_time_unix(after.content)
    if ts is not None:
        return ts

    ts = _extract_end_time_near_keyword(after.content)
    if ts is not None:
        return ts

    if after.edited_at:
        return after.edited_at.timestamp()
    return datetime.now(timezone.utc).timestamp()


def _update_state(guild_id: int, world_index: int, end_time: float) -> None:
    """讀取 hunt_state.json，更新對應伺服器的 kill_times[world_index] 後寫回。"""
    data: dict = {}
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 舊格式相容
            if "channel_id" in data:
                data = {}
        except (json.JSONDecodeError, IOError):
            data = {}

    key = str(guild_id)
    if key not in data:
        data[key] = {}

    kill_times = data[key].get("kill_times", [None] * len(WORLD_NAMES))
    if len(kill_times) < len(WORLD_NAMES):
        kill_times += [None] * (len(WORLD_NAMES) - len(kill_times))
    kill_times[world_index] = end_time
    data[key]["kill_times"] = kill_times

    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class SevenAMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        cfg = configparser.ConfigParser()
        cfg.read(_CONFIG_PATH, encoding="utf-8")
        self._channel_id = int(cfg.get("7a_monitor", "listen_channel", fallback="0").strip() or "0")
        self._role_id    = int(cfg.get("7a_monitor", "role_id",        fallback="0").strip() or "0")
        # 去重：記錄最近處理的 (message_id, content_hash)，避免 on_message + on_raw 雙觸發
        self._last_processed: dict[int, int] = {}  # msg_id → hash(content)

    async def _process_message(self, msg: discord.Message) -> None:
        """共用處理邏輯，供 on_message / on_raw_message_edit 呼叫。"""
        if msg.channel.id != self._channel_id:
            return

        # 去重：同一訊息相同內容不重複處理
        content_hash = hash(msg.content)
        if self._last_processed.get(msg.id) == content_hash:
            return
        self._last_processed[msg.id] = content_hash
        # 只留最近 50 筆，避免記憶體無限增長
        if len(self._last_processed) > 50:
            oldest = next(iter(self._last_processed))
            del self._last_processed[oldest]

        guild_id_str = str(msg.guild.id) if msg.guild else "DM"
        guild_name   = getattr(msg.guild, "name", None) or f"ID={guild_id_str}"
        prefix = f"[7A Monitor] Guild={guild_name} | MsgID={msg.id}"
        print(f"{prefix} | [入口] 正在處理訊息")

        if not any(r.id == self._role_id for r in getattr(msg, "role_mentions", [])):
            print(f"{prefix} | [略過] 未含指定 role mention (role_id={self._role_id})")
            return

        if not _has_end_signal(msg.content):
            print(f"{prefix} | [略過] 未含結束訊號（刪除線/關鍵字）")
            return

        # 含「結束時間」欄位但尚未填入時間 → 初始貼文，等待編輯後再處理
        has_end_time_field = bool(re.search(r'結束時間', msg.content))
        if has_end_time_field and _extract_end_time_unix(msg.content) is None:
            print(f"{prefix} | [略過] 含『結束時間』關鍵字但欄位尚無時間，等待編輯填入")
            return

        world_index = _match_world(msg.content)
        if world_index is None:
            print(f"{prefix} | [匹配失敗] 無法唯一匹配伺服器（0 或 ≥2 個）Content={msg.content!r}")
            return

        end_time = _resolve_end_time(msg)
        guild_id = msg.guild.id if msg.guild else 0

        try:
            _update_state(guild_id, world_index, end_time)
        except Exception as e:
            print(f"{prefix} | [STATE_ERROR] 寫入失敗: {e!r}")
            return

        end_dt = datetime.fromtimestamp(end_time, tz=_TZ_TW)
        print(
            f"{prefix} | [匹配成功] 已刷新《{WORLD_NAMES[world_index]}》擊殺時間 "
            f"→ {end_dt.strftime('%H:%M')} (ts={end_time:.0f})"
        )

        # 立即刷新面板 embed
        atrain = sys.modules.get("Functions.Cogs.ATrainOverview")
        if atrain:
            try:
                await atrain.refresh_panel(self.bot, guild_id)
            except Exception as e:
                print(f"{prefix} | [refresh_panel] failed: {e!r}")

    # ── 事件監聽 ────────────────────────────────────────────────────────────────

    async def _process_from_payload(self, payload: discord.RawMessageUpdateEvent) -> None:
        """當 fetch_message 因 403 失敗時，改用 gateway payload 的原始資料處理。"""
        content = payload.data.get('content', '')
        if not content:
            return

        guild_id   = payload.guild_id or 0
        guild      = self.bot.get_guild(guild_id) if guild_id else None
        guild_name = getattr(guild, 'name', None) or f"ID={guild_id}"
        msg_id     = payload.message_id
        prefix     = f"[7A Monitor] Guild={guild_name} | MsgID={msg_id}"
        print(f"{prefix} | [入口] 正在處理訊息（raw payload）")

        # 去重
        content_hash = hash(content)
        if self._last_processed.get(msg_id) == content_hash:
            return
        self._last_processed[msg_id] = content_hash
        if len(self._last_processed) > 50:
            oldest = next(iter(self._last_processed))
            del self._last_processed[oldest]

        # Role mention 檢查（mention_roles 為 role ID 字串列表）
        mention_roles = [str(r) for r in payload.data.get('mention_roles', [])]
        if str(self._role_id) not in mention_roles:
            print(f"{prefix} | [略過] 未含指定 role mention (role_id={self._role_id})")
            return

        if not _has_end_signal(content):
            print(f"{prefix} | [略過] 未含結束訊號（刪除線/關鍵字）")
            return

        has_end_time_field = bool(re.search(r'結束時間', content))
        if has_end_time_field and _extract_end_time_unix(content) is None:
            print(f"{prefix} | [略過] 含『結束時間』關鍵字但欄位尚無時間，等待編輯填入")
            return

        world_index = _match_world(content)
        if world_index is None:
            print(f"{prefix} | [匹配失敗] 無法唯一匹配伺服器 Content={content!r}")
            return

        # 解析結束時間
        now_tw = datetime.now(_TZ_TW)
        end_time = _extract_end_time_unix(content)
        if end_time is None:
            times = _parse_times(content)
            if times:
                h, mn = max(times, key=lambda t: t[0] * 60 + t[1])
                end_time = now_tw.replace(hour=h, minute=mn, second=0, microsecond=0).timestamp()
            else:
                edited_ts = payload.data.get('edited_timestamp')
                if edited_ts:
                    end_time = datetime.fromisoformat(edited_ts.replace('Z', '+00:00')).timestamp()
                else:
                    end_time = datetime.now(timezone.utc).timestamp()

        try:
            _update_state(guild_id, world_index, end_time)
        except Exception as e:
            print(f"{prefix} | [STATE_ERROR] 寫入失敗: {e!r}")
            return

        end_dt = datetime.fromtimestamp(end_time, tz=_TZ_TW)
        print(
            f"{prefix} | [匹配成功] 已刷新《{WORLD_NAMES[world_index]}》擊殺時間 "
            f"→ {end_dt.strftime('%H:%M')} (ts={end_time:.0f})"
        )

        atrain = sys.modules.get("Functions.Cogs.ATrainOverview")
        if atrain and guild_id:
            try:
                await atrain.refresh_panel(self.bot, guild_id)
            except Exception as e:
                print(f"{prefix} | [refresh_panel] failed: {e!r}")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        """
        使用 raw 事件，無論訊息是否在快取中都能觸發。
        on_message_edit 只在訊息已在快取時才觸發；bot 重啟後舊訊息的編輯會被漏掉。
        """
        if payload.channel_id != self._channel_id:
            return
        print(f"[7A Raw] channel={payload.channel_id} msg={payload.message_id}")

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
            await self._process_message(msg)
        except discord.Forbidden:
            print(f"[7A Raw] ⚠️ 缺少讀取訊息歷史權限，改用 gateway payload 處理")
            await self._process_from_payload(payload)
        except Exception as e:
            print(f"[7A Raw] 處理例外: {e!r}")


async def setup(bot: commands.Bot):
    await bot.add_cog(SevenAMonitor(bot))

