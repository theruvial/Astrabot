import discord
from discord.ext import commands
import asyncio
import os
import sqlite3
import certifi
from config import config

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

startup_extensions = [
    # "cogs.roles",
    # "cogs.points",
    "cogs.moderation",
    # "cogs.gambling",
    # "cogs.shop",
    # "cogs.pet",
    # "cogs.tasks",
    "cogs.music",
    "cogs.clip",
]


class Astrabot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=config["prefix"],
            case_insensitive=True,
            description=config["description"],
            intents=intents,
            heartbeat_timeout=150.0,
        )
        self.db = None
        self._synced_on_ready = False

    async def setup_hook(self):
        # Initialize SQLite connection
        db_name = config.get("db", "thi.db")
        if not db_name.endswith(".db"):
            db_name = f"{db_name}.db"
        self.db = sqlite3.connect(db_name)
        self.db.row_factory = sqlite3.Row

        # Create tables if they don't exist
        cursor = self.db.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS exp (
                ds INTEGER NOT NULL,
                discord_id INTEGER NOT NULL,
                exp INTEGER NOT NULL,
                type TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bucks (
                ds INTEGER NOT NULL,
                discord_id INTEGER NOT NULL,
                bucks INTEGER NOT NULL,
                type TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS jackpot (
                ds INTEGER NOT NULL,
                discord_id INTEGER NOT NULL,
                bucks INTEGER NOT NULL,
                type TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                isActive BOOLEAN NOT NULL,
                discordID BIGINT NOT NULL,
                ds BIGINT NOT NULL,
                taskName TEXT NOT NULL,
                description TEXT NOT NULL,
                assignedBy BIGINT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS roles (
                emojiID BIGINT NOT NULL,
                roleID BIGINT NOT NULL,
                roleDescription TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fct_pet_hp (
                discord_id BIGINT NOT NULL,
                cur_hp INTEGER NOT NULL,
                ds BIGINT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fct_pet_xp (
                discord_id BIGINT NOT NULL,
                xp BIGINT NOT NULL,
                ds BIGINT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fct_pet_stats (
                discord_id BIGINT NOT NULL,
                ds BIGINT NOT NULL,
                vitality INTEGER NOT NULL,
                attack INTEGER NOT NULL,
                defense INTEGER NOT NULL,
                speed INTEGER NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dim_pet (
                name TEXT NOT NULL,
                type TEXT NOT NULL
            )
        ''')
        self.db.commit()

        # Load extensions
        for extension in startup_extensions:
            try:
                await self.load_extension(extension)
                print(f"Loaded extension {extension}")
            except Exception as e:
                print(f"Failed to load extension {extension}\n{type(e).__name__}: {e}")

        # Sync global slash commands
        await self.tree.sync()

    async def on_ready(self):
        if not self._synced_on_ready:
            for guild in self.guilds:
                try:
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    names = ", ".join(c.name for c in synced)
                    print(f"Synced {len(synced)} commands to '{guild.name}' ({guild.id}): {names}")
                except Exception as e:
                    print(f"Failed to sync commands to guild {guild.id}: {e}")
            self._synced_on_ready = True

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="Metronome rant about girls",
            )
        )
        print(f"Logged in as {self.user.name} (ID: {self.user.id})")
        print("-" * 23)

    async def close(self):
        if self.db:
            self.db.close()
        await super().close()


async def main():
    bot = Astrabot()
    async with bot:
        await bot.start(config["token"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
