# logger.py
import discord
import asyncio
import aiohttp
import io
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

POLISH_TZ = timezone(timedelta(hours=2))

# ID kanałów na Serwerze 2 (backup — jeśli webhook nie działa)
LOG_CHANNELS = {
    "tickets":  1511718973224521728,
    "rekru":    1511719087531626586,
    "links":    1511719022381498518,
    "dziennik": 1511718924201496586,
    "slash":    1511720910263156888,
}

# Webhooki z .env
LOG_WEBHOOKS = {
    "tickets":  os.getenv("LOG_WEBHOOK_TICKETS"),
    "rekru":    os.getenv("LOG_WEBHOOK_REKRU"),
    "links":    os.getenv("LOG_WEBHOOK_LINKS"),
    "dziennik": os.getenv("LOG_WEBHOOK_DZIENNIK"),
    "slash":    os.getenv("LOG_WEBHOOK_SLASH"),
}


class ChannelLogger:
    _instance = None

    def __init__(self):
        self._bot = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._ready = False
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def get(cls) -> "ChannelLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_bot(self, bot):
        self._bot = bot

    async def start(self):
        if self._ready:
            return
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._worker())
        self._ready = True
        print("📡 ChannelLogger started (webhook + channel fallback)")

    async def close(self):
        self._ready = False
        if self._task:
            self._task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _worker(self):
        while True:
            try:
                key, embed, content, files_data = await self._queue.get()
                await self._send(key, embed, content, files_data)
                self._queue.task_done()
                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ ChannelLogger worker error: {e}")
                await asyncio.sleep(2)

    async def _send(self, key: str, embed, content, files_data):
        webhook_url = LOG_WEBHOOKS.get(key)

        if webhook_url and self._session and not self._session.closed:
            success = await self._send_via_webhook(webhook_url, embed, content, files_data)
            if success:
                return

        await self._send_to_channel(key, embed, content, files_data)

    async def _send_via_webhook(self, url: str, embed, content, files_data) -> bool:
        try:
            webhook = discord.Webhook.from_url(url, session=self._session)

            kwargs = {}
            if content:
                kwargs["content"] = content
            if embed:
                kwargs["embed"] = embed
            if files_data:
                files = [
                    discord.File(io.BytesIO(fb), filename=fn)
                    for fn, fb in files_data
                ]
                kwargs["files"] = files

            await webhook.send(**kwargs)
            return True

        except discord.NotFound:
            print(f"❌ Webhook nie istnieje: {url[:60]}...")
            return False
        except discord.Forbidden:
            print(f"❌ Webhook brak uprawnień: {url[:60]}...")
            return False
        except Exception as e:
            print(f"❌ Webhook error: {e}")
            return False

    async def _send_to_channel(self, key: str, embed, content, files_data):
        if not self._bot:
            return

        channel_id = LOG_CHANNELS.get(key)
        if not channel_id:
            return

        channel = self._bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self._bot.fetch_channel(channel_id)
            except Exception:
                return

        try:
            kwargs = {}
            if content:
                kwargs["content"] = content
            if embed:
                kwargs["embed"] = embed
            if files_data:
                kwargs["files"] = [
                    discord.File(io.BytesIO(fb), filename=fn)
                    for fn, fb in files_data
                ]
            await channel.send(**kwargs)
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"❌ Channel send error ({key}): {e}")

    # ─────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────

    def log(self, key: str, embed=None, content=None, files=None):
        if not self._ready:
            return
        self._queue.put_nowait((key, embed, content, files))

    def log_slash_command(
        self,
        interaction: discord.Interaction,
        extra_info: str = None,
    ):
        user = interaction.user
        ch = interaction.channel
        guild = interaction.guild
        cmd_name = (
            interaction.command.name if interaction.command else "unknown"
        )

        params_text = "Brak"
        try:
            if interaction.namespace:
                params = {k: str(v) for k, v in interaction.namespace}
                if params:
                    params_text = "\n".join(
                        f"`{k}`: {v}" for k, v in params.items()
                    )
        except Exception:
            pass

        embed = discord.Embed(
            title=f"⌨️ /{cmd_name}",
            color=0x5865F2,
            timestamp=datetime.now(POLISH_TZ),
        )
        embed.add_field(
            name="👤 Użytkownik",
            value=f"{user.mention} (`{user.id}`)\n`{user}`",
            inline=True,
        )
        embed.add_field(
            name="📍 Kanał",
            value=f"#{ch.name if ch else 'DM'}\n(`{ch.id if ch else 'N/A'}`)",
            inline=True,
        )
        embed.add_field(name="📋 Parametry", value=params_text, inline=False)
        if extra_info:
            embed.add_field(name="ℹ️ Info", value=extra_info, inline=False)
        embed.set_author(
            name=user.display_name, icon_url=user.display_avatar.url
        )
        if guild and guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        self.log("slash", embed=embed)

    def log_prefix_command(
        self,
        ctx,
        extra_info: str = None,
    ):
        """Loguje komendę prefixową ($acc, $dec, $close itp.) do webhooka slash."""
        user = ctx.author
        ch = ctx.channel
        guild = ctx.guild
        cmd_name = ctx.command.name if ctx.command else "unknown"

        # Pobierz argumenty wiadomości (bez prefiksu i nazwy komendy)
        args_text = "Brak"
        try:
            full_msg = ctx.message.content or ""
            # Usuwamy prefix + komendę
            prefix = ctx.prefix or "$"
            after_prefix = full_msg[len(prefix):]
            parts = after_prefix.split(None, 1)
            if len(parts) > 1:
                args_text = parts[1]
        except Exception:
            pass

        embed = discord.Embed(
            title=f"⌨️ ${cmd_name}",
            color=0xEB459E,  # różowy — odróżnia prefix od slash
            timestamp=datetime.now(POLISH_TZ),
        )
        embed.add_field(
            name="👤 Użytkownik",
            value=f"{user.mention} (`{user.id}`)\n`{user}`",
            inline=True,
        )
        embed.add_field(
            name="📍 Kanał",
            value=f"#{ch.name if ch else 'DM'}\n(`{ch.id if ch else 'N/A'}`)",
            inline=True,
        )
        embed.add_field(
            name="📋 Argumenty",
            value=f"`{args_text}`" if args_text != "Brak" else "Brak",
            inline=False,
        )
        if extra_info:
            embed.add_field(name="ℹ️ Info", value=extra_info, inline=False)
        embed.set_author(
            name=user.display_name, icon_url=user.display_avatar.url
        )
        if guild and guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        self.log("slash", embed=embed)

    def log_dziennik(
        self,
        admin,
        target,
        action,
        reason,
        guild,
        status_before="",
        status_after="",
        color=0x0A8AF2,
    ):
        embed = discord.Embed(
            title=f"📋 Dziennik — {action}",
            color=color,
            timestamp=datetime.now(POLISH_TZ),
        )
        embed.add_field(
            name="👤 Target",
            value=f"{target.mention} (`{target.id}`)",
            inline=True,
        )
        embed.add_field(
            name="🛡️ Administrator",
            value=f"{admin.mention} (`{admin.id}`)",
            inline=True,
        )
        embed.add_field(
            name="Akcja", value=f"```{action}```", inline=False
        )
        if status_before or status_after:
            st = ""
            if status_before:
                st += f"**Przed:** {status_before}\n"
            if status_after:
                st += f"**Po:** {status_after}"
            embed.add_field(name="Status", value=st, inline=False)
        embed.add_field(
            name="Powód", value=f"```{reason or 'Brak'}```", inline=False
        )
        embed.set_author(
            name=admin.display_name, icon_url=admin.display_avatar.url
        )
        if guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        self.log("dziennik", embed=embed)

    def log_ticket(
        self,
        action,
        ticket_name,
        user,
        guild,
        description=None,
        color=0x0A8AF2,
        files=None,
    ):
        embed = discord.Embed(
            title=f"🎫 {action}",
            description=description or "",
            color=color,
            timestamp=datetime.now(POLISH_TZ),
        )
        embed.add_field(name="Ticket", value=f"`{ticket_name}`", inline=True)
        embed.add_field(
            name="Użytkownik",
            value=f"{user.mention} (`{user.id}`)",
            inline=True,
        )
        embed.set_author(
            name=user.display_name, icon_url=user.display_avatar.url
        )
        if guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        self.log("tickets", embed=embed, files=files)

    def log_rekru(self, reviewer, target, decision, guild, extra=None):
        is_acc = decision.upper() in ("ACC", "ACCEPT", "ACCEPTED")
        embed = discord.Embed(
            title=f"📝 Rekrutacja — {'✅ ACC' if is_acc else '❌ DEC'}",
            color=0x2ECC71 if is_acc else 0xE74C3C,
            timestamp=datetime.now(POLISH_TZ),
        )
        embed.add_field(
            name="👤 Kandydat",
            value=f"{target.mention} (`{target.id}`)",
            inline=True,
        )
        embed.add_field(
            name="🛡️ Recenzent",
            value=f"{reviewer.mention} (`{reviewer.id}`)",
            inline=True,
        )
        embed.add_field(
            name="Decyzja",
            value=f"```{decision.upper()}```",
            inline=False,
        )
        if extra:
            embed.add_field(name="ℹ️ Info", value=extra, inline=False)
        embed.set_author(
            name=reviewer.display_name, icon_url=reviewer.display_avatar.url
        )
        if guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        self.log("rekru", embed=embed)

    def log_links(
        self,
        user,
        channel,
        guild,
        link,
        action="Link wykryty",
        blocked=False,
    ):
        embed = discord.Embed(
            title=f"🔗 {action}",
            color=0xE74C3C if blocked else 0xF39C12,
            timestamp=datetime.now(POLISH_TZ),
        )
        embed.add_field(
            name="👤 Użytkownik",
            value=f"{user.mention} (`{user.id}`)",
            inline=True,
        )
        embed.add_field(
            name="📍 Kanał",
            value=f"#{channel.name} (`{channel.id}`)",
            inline=True,
        )
        embed.add_field(
            name="🔗 Link", value=f"```{link[:500]}```", inline=False
        )
        embed.add_field(
            name="Status",
            value="🚫 Zablokowany" if blocked else "⚠️ Przepuszczony",
            inline=False,
        )
        embed.set_author(
            name=user.display_name, icon_url=user.display_avatar.url
        )
        if guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        self.log("links", embed=embed)

    def log_embed(
        self,
        key: str,
        title=None,
        description=None,
        color=0x0A8AF2,
        fields=None,
        footer=None,
        author_name=None,
        author_icon=None,
        thumbnail=None,
        image=None,
    ):
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.now(POLISH_TZ),
        )
        if fields:
            for n, v, i in fields:
                embed.add_field(name=n, value=v, inline=i)
        if footer:
            embed.set_footer(text=footer)
        if author_name:
            embed.set_author(name=author_name, icon_url=author_icon)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if image:
            embed.set_image(url=image)
        self.log(key, embed=embed)


def get_logger() -> ChannelLogger:
    return ChannelLogger.get()


# =========================================
# ALIAS — kompatybilność z istniejącymi plikami
# =========================================
class LogChannel:
    """Mapuje stałe na klucze stringowe używane przez ChannelLogger."""
    TICKETS  = "tickets"
    REKRU    = "rekru"
    LINKS    = "links"
    DZIENNIK = "dziennik"
    SLASH    = "slash"