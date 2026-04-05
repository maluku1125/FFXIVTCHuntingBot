import datetime
import discord
from discord import app_commands
from discord.ext import commands
import configparser
import asyncio



try:
    _HuntBot_CONF = configparser.ConfigParser()
    config_path =  "C:\\Users\\User\\Desktop\\FFXIVTCHuntingBot\\Config\\FFXIVTC-Huntingbot_config.ini"
    _HuntBot_CONF.read(config_path, encoding="utf-8")
except FileNotFoundError:
    print(f"`{config_path}` file missing.")
    #sys.exit(1)

discord.voice_client.VoiceClient.warn_nacl = False


def resolve_intents() -> discord.Intents:
    "Resolves configured intents to discord format"
    intents = discord.Intents.all()
    return intents

class HuntBot(commands.AutoShardedBot):

    def __init__(self, config, intents):
        allowed_mentions = discord.AllowedMentions(
            roles=True, everyone=False, users=True, replied_user=False
        )
        super().__init__(
            self_bot=True,
            command_prefix=commands.when_mentioned_or(
                config["bot"]["prefix"].strip('"')
            ),
            description=config["bot"]["description"],
            pm_help=True,
            heartbeat_timeout=150.0,
            allowed_mentions=allowed_mentions,
            intents=intents,
            activity=discord.Activity(
                type=discord.ActivityType.playing, name=config["bot"]["activity"]
            ),
        )
      
        # setup from config
        self._config = config
        self.color = discord.Color.from_str(config["bot"]["color"])
        self.name = config["bot"]["name"]
        self.session = None
        self.uptime = None
        self.time_date = ''
        
        print('-'*25)
        print('HuntBot is Loading')
        print('-'*25)

    async def setup_hook(self):
        await self.load_extension("Functions.Cogs.ATrainOverview")
        print("Cog: ATrainOverview loaded")
        await self.load_extension("Functions.Cogs.SRebornspot")
        print("Cog: SRebornspot loaded")
        await self.load_extension("Functions.Cogs.MessageLogger")
        print("Cog: MessageLogger loaded")
        await self.load_extension("Functions.Cogs.EorzeaTime")
        print("Cog: EorzeaTime loaded")
        await self.load_extension("Functions.Cogs.Weather")
        print("Cog: Weather loaded")
        await self.load_extension("Functions.Cogs.SRankSpecial")
        print("Cog: SRankSpecial loaded")

        # 讓所有 slash command 支援個人安裝（User Install）
        _install = app_commands.AppInstallationType(guild=True, user=True)
        _ctx = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True)
        for cmd in self.tree.get_commands():
            cmd.allowed_installs = _install
            cmd.allowed_contexts = _ctx

    async def on_ready(self):
        dev_guild_id = self._config["bot"]["dev_guild"].strip()
        print('slash command is now loading')
        print(f'devguild : {dev_guild_id}')

        # 必須做全域同步，User Install 的個人用戶才能看到指令
        slash = await self.tree.sync()
        print(f"Loaded slash command to global (User Install support)")

        if dev_guild_id:
            # 額外同步到 dev guild，讓測試伺服器立即生效（無需等全域傳播）
            dev_guild = self.get_guild(int(dev_guild_id))
            if dev_guild:
                self.tree.copy_global_to(guild=dev_guild)
                await self.tree.sync(guild=dev_guild)
                print(f"Also synced to dev guild (instant)")

        print(f"Total Slash Command Loaded:{len(slash)}")

        print('-'*25)
        print('HuntBot is Online')
        print('-'*25)

    async def on_guild_join(self, guild):
        
        print(f'Joined new guild: {guild.name} (id: {guild.id})')
        print(f'Currently in {len(self.guilds)} guilds')
        print('-'*25)


async def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    FFXIVTC_Huntbot = HuntBot(config=_HuntBot_CONF, intents=resolve_intents())

    await FFXIVTC_Huntbot.start(_HuntBot_CONF["discord"]["token"], reconnect=True)

if __name__ == "__main__":
    asyncio.run(main())