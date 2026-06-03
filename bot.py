import discord
from discord.ext import commands
import pymysql
import os
import asyncio
import aiohttp
import io
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pathlib import Path
from flask import Flask
from threading import Thread

from logger import ChannelLogger, get_logger

load_dotenv()

# =========================================
# FLASK KEEP-ALIVE (dla Render darmowy plan)
# =========================================
app = Flask('')

@app.route('/')
def home():
    return "✅ Bot is alive!"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()
    print(f"✅ Flask keep-alive uruchomiony")

# =========================================
# KONFIGURACJA
# =========================================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.moderation = True

GUILD_ID = 1511465367095218368
ADMIN_ROLE_ID = 1511465367095218370
POLISH_TZ = timezone(timedelta(hours=2))

STATUS_CHANNEL_ID = 1511749668642619532
STATUS_MESSAGE_FILE = "status_message_id.txt"


# =========================================
# BAZA DANYCH
# =========================================
def get_db():
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
    )


def save_stats(guild_id, metric_name, metric_value):
    db = None
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id FROM stats WHERE guild_id = %s AND metric_name = %s",
                (guild_id, metric_name),
            )
            if cur.fetchone():
                cur.execute(
                    "UPDATE stats SET metric_value = %s, updated_at = NOW() "
                    "WHERE guild_id = %s AND metric_name = %s",
                    (metric_value, guild_id, metric_name),
                )
            else:
                cur.execute(
                    "INSERT INTO stats (guild_id, metric_name, metric_value, updated_at) "
                    "VALUES (%s, %s, %s, NOW())",
                    (guild_id, metric_name, metric_value),
                )
        db.commit()
        return True
    except Exception as e:
        print(f"❌ Błąd save_stats: {e}")
        return False
    finally:
        if db:
            db.close()


def log_to_db(guild_id, user_id, username, action, log_type):
    db = None
    try:
        db = get_db()
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO logs
                (guild_id, user_id, user_name, action, log_type, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (
                    str(guild_id),
                    str(user_id),
                    str(username),
                    str(action),
                    str(log_type),
                ),
            )
        db.commit()
    except Exception as e:
        print(f"❌ BŁĄD log_to_db: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if db:
            db.close()


# =========================================
# HELPERY
# =========================================
def to_polish_time(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(POLISH_TZ)


def _load_status_message_id() -> int | None:
    try:
        if os.path.exists(STATUS_MESSAGE_FILE):
            with open(STATUS_MESSAGE_FILE, "r") as f:
                val = f.read().strip()
                return int(val) if val.isdigit() else None
    except Exception:
        pass
    return None


def _save_status_message_id(message_id: int):
    try:
        with open(STATUS_MESSAGE_FILE, "w") as f:
            f.write(str(message_id))
    except Exception as e:
        print(f"❌ Nie można zapisać status message ID: {e}")


# =========================================
# BOT
# =========================================
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="$", intents=intents)
        self.GUILD_ID = GUILD_ID
        self.ADMIN_ROLE_ID = ADMIN_ROLE_ID
        self.channel_logger: ChannelLogger = get_logger()
        self._status_message_id: int | None = None

    # =========================================
    # GLOBALNY LOG WSZYSTKICH PREFIX KOMEND
    # =========================================
    async def on_command(self, ctx: commands.Context):
        try:
            self.channel_logger.log_prefix_command(ctx)
        except Exception as e:
            print(f"❌ Błąd logowania prefix command: {e}")

        try:
            if ctx.guild and ctx.channel.permissions_for(
                ctx.guild.me
            ).manage_messages:
                await ctx.message.delete()
        except Exception:
            pass

    async def load_cogs(self):
        folders_to_load = ["administration", "security", "community", "system"]
        loaded = 0

        for folder in folders_to_load:
            folder_path = Path(folder)
            if not folder_path.exists():
                print(f"❌ Brak folderu '{folder}'!")
                continue

            for py_file in folder_path.glob("*.py"):
                if py_file.name == "__init__.py":
                    continue

                module_path = f"{folder}.{py_file.stem}"
                try:
                    await self.load_extension(module_path)
                    print(f"🛡️ Załadowano: {module_path}")
                    loaded += 1
                except Exception as e:
                    print(f"❌ Błąd {module_path}: {e}")
                    import traceback
                    traceback.print_exc()

        print(f"📦 Załadowano {loaded} modułów")

    async def setup_hook(self):
        print("🔧 Ładowanie modułów...")

        self.channel_logger.set_bot(self)
        await self.channel_logger.start()

        await self.load_cogs()

        try:
            guild = discord.Object(id=self.GUILD_ID)
            synced = await self.tree.sync(guild=guild)
            print(f"🛠️ Zsynchronizowano {len(synced)} komend slash")
        except Exception as e:
            print(f"❌ Błąd synchronizacji: {e}")
            import traceback
            traceback.print_exc()

    async def on_ready(self):
        print(f"\n{'=' * 50}")
        print(f"🖥️ Bot {self.user} online")
        print(f"🛠️ Serwery: {len(self.guilds)}")
        print(f"👥 Użytkownicy: {sum(len(g.members) for g in self.guilds)}")
        print(f"{'=' * 50}\n")

        self.channel_logger.log_embed(
            "slash",
            title="🟢 Bot Online",
            description=(
                f"**Bot:** {self.user}\n"
                f"**Serwery:** {len(self.guilds)}\n"
                f"**Użytkownicy:** {sum(len(g.members) for g in self.guilds)}"
            ),
            color=0x2ECC71,
            footer="Bot startup",
        )

        await self._update_status_message()
        await self.update_stats()
        self.loop.create_task(self.update_stats_loop())

    async def _update_status_message(self):
        channel = self.get_channel(STATUS_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.fetch_channel(STATUS_CHANNEL_ID)
            except Exception as e:
                print(f"❌ Nie znaleziono kanału statusu ({STATUS_CHANNEL_ID}): {e}")
                return

        now = datetime.now(POLISH_TZ)
        guild = self.get_guild(self.GUILD_ID)
        members_count = (
            sum(1 for m in guild.members if not m.bot) if guild else "?"
        )

        embed = discord.Embed(
            title="🖥️ Status Bota",
            color=0x2ECC71,
            timestamp=now,
        )
        embed.add_field(name="🤖 Bot", value=str(self.user), inline=True)
        embed.add_field(name="🟢 Status", value="**Online**", inline=True)
        embed.add_field(
            name="🔄 Ostatni restart",
            value=f"<t:{int(now.timestamp())}:F>",
            inline=False,
        )
        embed.add_field(name="🛠️ Serwery", value=str(len(self.guilds)), inline=True)
        embed.add_field(name="👥 Użytkownicy", value=str(members_count), inline=True)
        embed.set_footer(text="Aktualizowane przy każdym restarcie")
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        saved_id = _load_status_message_id()
        message = None

        if saved_id:
            try:
                message = await channel.fetch_message(saved_id)
            except (discord.NotFound, discord.HTTPException):
                message = None

        if message:
            try:
                await message.edit(embed=embed)
                print(f"✅ Status wiadomość edytowana (ID: {message.id})")
                return
            except Exception as e:
                print(f"⚠️ Nie można edytować statusu: {e}")
                message = None

        try:
            new_msg = await channel.send(embed=embed)
            _save_status_message_id(new_msg.id)
            print(f"✅ Status wiadomość wysłana (ID: {new_msg.id})")
        except Exception as e:
            print(f"❌ Nie można wysłać statusu: {e}")

    async def close(self):
        try:
            channel = self.get_channel(STATUS_CHANNEL_ID)
            if channel is None:
                channel = await self.fetch_channel(STATUS_CHANNEL_ID)

            saved_id = _load_status_message_id()
            if saved_id and channel:
                try:
                    message = await channel.fetch_message(saved_id)
                    now = datetime.now(POLISH_TZ)
                    embed = discord.Embed(
                        title="🖥️ Status Bota",
                        color=0xE74C3C,
                        timestamp=now,
                    )
                    embed.add_field(name="🤖 Bot", value=str(self.user), inline=True)
                    embed.add_field(name="🔴 Status", value="**Offline**", inline=True)
                    embed.add_field(
                        name="⏹️ Wyłączono",
                        value=f"<t:{int(now.timestamp())}:F>",
                        inline=False,
                    )
                    embed.set_footer(text="Aktualizowane przy każdym restarcie")
                    await message.edit(embed=embed)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.channel_logger.log_embed(
                "slash",
                title="🔴 Bot Offline",
                description=f"**Bot:** {self.user}",
                color=0xE74C3C,
                footer="Bot shutdown",
            )
            await asyncio.sleep(1)
        except Exception:
            pass

        await self.channel_logger.close()
        await super().close()

    # =========================================
    # GLOBALNY LOG WSZYSTKICH SLASH KOMEND
    # =========================================
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: discord.app_commands.Command | discord.app_commands.ContextMenu,
    ):
        try:
            self.channel_logger.log_slash_command(interaction)
        except Exception as e:
            print(f"❌ Błąd logowania slash command: {e}")

    # =========================================
    # STATYSTYKI
    # =========================================
    async def update_stats(self):
        try:
            guild = self.get_guild(self.GUILD_ID)
            if not guild:
                print(f"⚠️ Nie znaleziono serwera {self.GUILD_ID}")
                return False

            members_count = sum(1 for m in guild.members if not m.bot)
            save_stats(str(self.GUILD_ID), "members", members_count)

            admin_role = guild.get_role(self.ADMIN_ROLE_ID)
            admin_count = (
                sum(1 for m in admin_role.members if not m.bot)
                if admin_role
                else 0
            )

            if admin_role:
                print(
                    f"✅ Statystyki: {members_count} członków, "
                    f"{admin_count} adminów (rola: {admin_role.name})"
                )
            else:
                print(f"⚠️ Nie znaleziono roli admina (ID: {self.ADMIN_ROLE_ID})")

            save_stats(str(self.GUILD_ID), "admins", admin_count)
            return True
        except Exception as e:
            print(f"❌ Błąd update_stats: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def update_stats_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await asyncio.sleep(60)
                await self.update_stats()
            except Exception as e:
                print(f"❌ Błąd update_stats_loop: {e}")
                await asyncio.sleep(10)

    # =========================================
    # EVENTY
    # =========================================
    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        if guild.id != self.GUILD_ID:
            return

        executor_name, executor_id, reason = "System", "0", "Brak"
        try:
            async for entry in guild.audit_logs(
                limit=1, action=discord.AuditLogAction.ban
            ):
                if entry.target.id == user.id:
                    executor_name = str(entry.user)
                    executor_id = str(entry.user.id)
                    reason = entry.reason or "Brak"
                    break
        except Exception:
            pass

        log_to_db(
            guild.id,
            executor_id,
            executor_name,
            f"𝗕𝗮𝗻 𝗡𝗮𝗱𝗮𝗻𝘆 - {user} ({user.id}) | 𝗣𝗼𝘄𝗼́𝗱 - {reason}",
            "discord",
        )
        print(f"𝗕𝗮𝗻 𝗡𝗮𝗱𝗮𝗻𝘆 - {user}")

        self.channel_logger.log_embed(
            "slash",
            title="🔨 Ban Nadany",
            description=(
                f"**Użytkownik:** {user} (`{user.id}`)\n"
                f"**Wykonał:** {executor_name} (`{executor_id}`)\n"
                f"**Powód:** ```{reason}```"
            ),
            color=0xE74C3C,
            footer=guild.name,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        if guild.id != self.GUILD_ID:
            return

        executor_name, executor_id, reason = "System", "0", "Brak"
        try:
            async for entry in guild.audit_logs(
                limit=1, action=discord.AuditLogAction.unban
            ):
                if entry.target.id == user.id:
                    executor_name = str(entry.user)
                    executor_id = str(entry.user.id)
                    reason = entry.reason or "Brak"
                    break
        except Exception:
            pass

        log_to_db(
            guild.id,
            executor_id,
            executor_name,
            f"Unban - {user} ({user.id}) | Powód - {reason}",
            "discord",
        )

        self.channel_logger.log_embed(
            "slash",
            title="🔓 Unban",
            description=(
                f"**Użytkownik:** {user} (`{user.id}`)\n"
                f"**Wykonał:** {executor_name} (`{executor_id}`)\n"
                f"**Powód:** ```{reason}```"
            ),
            color=0x2ECC71,
            footer=guild.name,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.guild.id != self.GUILD_ID:
            return

        try:
            guild = member.guild
            async for entry in guild.audit_logs(
                limit=1, action=discord.AuditLogAction.kick
            ):
                if entry.target.id == member.id:
                    executor_name = str(entry.user)
                    executor_id = str(entry.user.id)
                    reason = entry.reason or "Brak"

                    log_to_db(
                        guild.id,
                        executor_id,
                        executor_name,
                        f"Kick - {member} ({member.id}) | Powód - {reason}",
                        "discord",
                    )

                    self.channel_logger.log_embed(
                        "slash",
                        title="👢 Kick",
                        description=(
                            f"**Użytkownik:** {member} (`{member.id}`)\n"
                            f"**Wykonał:** {executor_name} (`{executor_id}`)\n"
                            f"**Powód:** ```{reason}```"
                        ),
                        color=0xF39C12,
                        footer=guild.name,
                    )
                    return
        except Exception:
            pass


# =========================================
# START
# =========================================
bot = MyBot()

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ Brak tokenu DISCORD_TOKEN w .env!")
        exit(1)

    # Uruchom Flask keep-alive PRZED botem
    keep_alive()

    bot.run(TOKEN)
