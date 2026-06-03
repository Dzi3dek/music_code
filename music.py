import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import ctypes
import ctypes.util
import subprocess
import sys
from datetime import timedelta
from typing import List, Optional

# ============================================================
# ŁADOWANIE OPUS
# ============================================================
def load_opus_lib():
    if discord.opus.is_loaded():
        print("✅ [Music] Opus już załadowany")
        return True

    # === PRÓBA 1: Standardowe ścieżki ===
    opus_libs = [
        'libopus.so.0',
        'libopus.so',
        'libopus',
        '/usr/lib/x86_64-linux-gnu/libopus.so.0',
        '/usr/lib/aarch64-linux-gnu/libopus.so.0',
        '/usr/lib/arm-linux-gnueabihf/libopus.so.0',
        '/usr/local/lib/libopus.so',
        '/usr/local/lib/libopus.so.0',
        'opus',
    ]

    for lib in opus_libs:
        try:
            discord.opus.load_opus(lib)
            if discord.opus.is_loaded():
                print(f"✅ [Music] Opus załadowany z: {lib}")
                return True
        except Exception:
            continue

    # === PRÓBA 2: ctypes.util.find_library ===
    found = ctypes.util.find_library('opus')
    if found:
        try:
            discord.opus.load_opus(found)
            if discord.opus.is_loaded():
                print(f"✅ [Music] Opus załadowany przez ctypes: {found}")
                return True
        except Exception:
            pass

    # === PRÓBA 3: apt-get install ===
    try:
        print("⚠️ [Music] Próba instalacji libopus0 przez apt-get...")
        subprocess.run(
            ['apt-get', 'install', '-y', 'libopus0', 'ffmpeg'],
            capture_output=True,
            timeout=60
        )
        # Spróbuj ponownie po instalacji
        for lib in opus_libs:
            try:
                discord.opus.load_opus(lib)
                if discord.opus.is_loaded():
                    print(f"✅ [Music] Opus załadowany po apt-get: {lib}")
                    return True
            except Exception:
                continue
    except Exception as e:
        print(f"⚠️ [Music] apt-get failed: {e}")

    # === PRÓBA 4: pip install opuslib ===
    try:
        print("⚠️ [Music] Próba instalacji opuslib przez pip...")
        subprocess.check_call(
            [sys.executable, '-m', 'pip', 'install', '--prefix', '/home/container/.local', 'opuslib'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"⚠️ [Music] pip install opuslib failed: {e}")

    # === PRÓBA 5: Znajdź plik .so w całym systemie ===
    try:
        result = subprocess.run(
            ['find', '/', '-name', 'libopus.so*', '-type', 'f'],
            capture_output=True,
            text=True,
            timeout=10
        )
        paths = result.stdout.strip().split('\n')
        for path in paths:
            if path:
                try:
                    discord.opus.load_opus(path)
                    if discord.opus.is_loaded():
                        print(f"✅ [Music] Opus znaleziony przez find: {path}")
                        return True
                except Exception:
                    continue
    except Exception as e:
        print(f"⚠️ [Music] find failed: {e}")

    print("❌ [Music] Nie udało się załadować opus!")
    print("❌ [Music] Zainstaluj: apt-get install -y libopus0 ffmpeg")
    return False

# ============================================================
# OPCJE YT-DLP I FFMPEG
# ============================================================
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# ============================================================
# KLASA SONG
# ============================================================
class Song:
    def __init__(self, title: str, url: str, duration: int, requester: discord.Member, webpage_url: str):
        self.title = title
        self.url = url
        self.duration = duration
        self.requester = requester
        self.webpage_url = webpage_url

    @property
    def duration_formatted(self) -> str:
        if self.duration == 0:
            return "🔴 LIVE"
        minutes, seconds = divmod(self.duration, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

# ============================================================
# GŁÓWNA KLASA COG
# ============================================================
class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue: List[Song] = []
        self.current: Optional[Song] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.is_playing = False
        self.is_paused = False
        self.loop = False
        self.volume = 0.5
        self.ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

        # Załaduj opus przy starcie
        load_opus_lib()

    # --------------------------------------------------------
    # POMOCNICZE
    # --------------------------------------------------------
    def _get_vc(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        """Zwraca aktywny VoiceClient"""
        if ctx.voice_client:
            return ctx.voice_client
        return self.voice_client

    async def _join_voice(self, ctx: commands.Context) -> bool:
        """Dołącza do kanału głosowego użytkownika"""
        if not ctx.author.voice:
            await ctx.send("❌ Musisz być na kanale głosowym!")
            return False

        # Sprawdź czy opus jest załadowany
        if not discord.opus.is_loaded():
            if not load_opus_lib():
                await ctx.send(
                    "❌ **Brak biblioteki Opus na serwerze!**\n"
                    "```\n"
                    "Wymagane pakiety systemowe:\n"
                    "  apt-get install -y libopus0 ffmpeg\n"
                    "```\n"
                    "Skontaktuj się z administratorem hostingu."
                )
                return False

        channel = ctx.author.voice.channel
        vc = self._get_vc(ctx)

        try:
            if vc and vc.is_connected():
                if vc.channel.id != channel.id:
                    await vc.move_to(channel)
                self.voice_client = vc
            else:
                vc = await channel.connect(timeout=10.0, reconnect=True)
                self.voice_client = vc
            return True
        except asyncio.TimeoutError:
            await ctx.send("❌ Timeout - nie udało się połączyć z kanałem głosowym!")
            return False
        except discord.ClientException as e:
            await ctx.send(f"❌ Błąd klienta Discord: `{e}`")
            return False
        except Exception as e:
            await ctx.send(f"❌ Nie mogę dołączyć na kanał: `{e}`")
            return False

    # --------------------------------------------------------
    # LOGIKA ODTWARZANIA
    # --------------------------------------------------------
    def _play_next(self, error=None):
        if error:
            print(f"❌ [Music] Błąd odtwarzania: {error}")

        if self.loop and self.current:
            coro = self._play_song(self.current)
            self.bot.loop.create_task(coro)
            return

        if self.queue:
            self.current = self.queue.pop(0)
            coro = self._play_song(self.current)
            self.bot.loop.create_task(coro)
        else:
            self.is_playing = False
            self.current = None

    async def _play_song(self, song: Song):
        if not self.voice_client or not self.voice_client.is_connected():
            print("❌ [Music] Brak voice_client lub rozłączony")
            return

        self.is_playing = True
        self.is_paused = False

        try:
            source = discord.FFmpegPCMAudio(song.url, **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(source, volume=self.volume)
            self.voice_client.play(source, after=self._play_next)
        except discord.ClientException as e:
            print(f"❌ [Music] ClientException: {e}")
            channel = self.voice_client.channel
            await channel.send(f"❌ Błąd odtwarzania (ClientException): `{e}`")
            self._play_next()
        except Exception as e:
            print(f"❌ [Music] Exception: {e}")
            channel = self.voice_client.channel
            await channel.send(f"❌ Błąd podczas odtwarzania **{song.title}**: `{e}`")
            self._play_next()

    async def _search_song(self, query: str, requester: discord.Member) -> Optional[Song]:
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None,
                lambda: self.ytdl.extract_info(query, download=False)
            )
        except yt_dlp.utils.DownloadError as e:
            print(f"❌ [Music] DownloadError: {e}")
            return None
        except Exception as e:
            print(f"❌ [Music] Błąd wyszukiwania: {e}")
            return None

        if not data:
            return None

        if 'entries' in data:
            data = data['entries'][0] if data['entries'] else None

        if not data:
            return None

        return Song(
            title=data.get('title', 'Nieznany tytuł'),
            url=data['url'],
            duration=data.get('duration', 0) or 0,
            requester=requester,
            webpage_url=data.get('webpage_url', query)
        )

    # --------------------------------------------------------
    # KOMENDY
    # --------------------------------------------------------
    @commands.command(aliases=['p'])
    async def play(self, ctx: commands.Context, *, query: str):
        """▶️ Odtwarza muzykę | $play <tytuł lub link>"""
        if not await self._join_voice(ctx):
            return

        async with ctx.typing():
            song = await self._search_song(query, ctx.author)
            if not song:
                await ctx.send(f"❌ Nie znalazłem nic dla: **{query}**")
                return

        if self.is_playing or self.queue:
            self.queue.append(song)
            embed = discord.Embed(
                title="➕ Dodano do kolejki",
                description=f"**[{song.title}]({song.webpage_url})**",
                color=0x3498DB
            )
            embed.add_field(name="⏱️ Długość", value=song.duration_formatted, inline=True)
            embed.add_field(name="👤 Dodał", value=song.requester.mention, inline=True)
            embed.add_field(name="📌 Pozycja", value=str(len(self.queue)), inline=True)
            await ctx.send(embed=embed)
        else:
            self.current = song
            await self._play_song(song)
            embed = discord.Embed(
                title="▶️ Teraz gra",
                description=f"**[{song.title}]({song.webpage_url})**",
                color=0x2ECC71
            )
            embed.add_field(name="⏱️ Długość", value=song.duration_formatted, inline=True)
            embed.add_field(name="👤 Dodał", value=song.requester.mention, inline=True)
            await ctx.send(embed=embed)

    @commands.command(aliases=['s'])
    async def skip(self, ctx: commands.Context):
        """⏭️ Pomija aktualny utwór"""
        vc = self._get_vc(ctx)
        if not vc or not vc.is_playing():
            await ctx.send("❌ Nic teraz nie gra!")
            return

        skipped_title = self.current.title if self.current else "nieznany"

        if self.loop:
            self.loop = False
            await ctx.send("🔁 Pętla wyłączona")

        vc.stop()
        await ctx.send(f"⏭️ Pominięto: **{skipped_title}**")

    @commands.command()
    async def stop(self, ctx: commands.Context):
        """⏹️ Zatrzymuje muzykę i wychodzi z kanału"""
        vc = self._get_vc(ctx)
        if not vc:
            await ctx.send("❌ Nie jestem na kanale głosowym!")
            return

        self.queue.clear()
        self.current = None
        self.is_playing = False
        self.is_paused = False
        self.loop = False

        if vc.is_playing():
            vc.stop()

        await vc.disconnect()
        self.voice_client = None
        await ctx.send("⏹️ Zatrzymano i opuszczono kanał")

    @commands.command()
    async def pause(self, ctx: commands.Context):
        """⏸️ Pauzuje odtwarzanie"""
        vc = self._get_vc(ctx)
        if not vc or not vc.is_playing():
            await ctx.send("❌ Nic teraz nie gra!")
            return
        if self.is_paused:
            await ctx.send("❌ Muzyka jest już spauzowana!")
            return

        vc.pause()
        self.is_paused = True
        await ctx.send("⏸️ Spauzowano")

    @commands.command()
    async def resume(self, ctx: commands.Context):
        """▶️ Wznawia odtwarzanie"""
        vc = self._get_vc(ctx)
        if not vc:
            await ctx.send("❌ Nie jestem na kanale!")
            return
        if not self.is_paused:
            await ctx.send("❌ Muzyka nie jest spauzowana!")
            return

        vc.resume()
        self.is_paused = False
        await ctx.send("▶️ Wznowiono")

    @commands.command(aliases=['q'])
    async def kolejka(self, ctx: commands.Context):
        """📋 Pokazuje kolejkę utworów"""
        if not self.current and not self.queue:
            await ctx.send("📭 Kolejka jest pusta!")
            return

        embed = discord.Embed(title="📋 Kolejka muzyczna", color=0x9B59B6)

        if self.current:
            status = "⏸️" if self.is_paused else "▶️"
            loop_txt = " 🔁" if self.loop else ""
            embed.add_field(
                name=f"{status} Teraz gra{loop_txt}",
                value=f"**[{self.current.title}]({self.current.webpage_url})**\n"
                      f"⏱️ {self.current.duration_formatted} | 👤 {self.current.requester.mention}",
                inline=False
            )

        if self.queue:
            lines = []
            for i, song in enumerate(self.queue[:10], 1):
                lines.append(
                    f"`{i}.` **{song.title}** | {song.duration_formatted} | {song.requester.mention}"
                )
            txt = "\n".join(lines)
            if len(self.queue) > 10:
                txt += f"\n\n...i **{len(self.queue) - 10}** więcej"
            embed.add_field(name="🎵 Następne", value=txt, inline=False)

        total = sum(s.duration for s in self.queue)
        if self.current:
            total += self.current.duration
        if total > 0:
            embed.set_footer(text=f"Łączny czas: {timedelta(seconds=total)}")

        await ctx.send(embed=embed)

    @commands.command(aliases=['np', 'now'])
    async def nowplaying(self, ctx: commands.Context):
        """🎵 Pokazuje aktualnie grany utwór"""
        if not self.current:
            await ctx.send("❌ Nic teraz nie gra!")
            return

        status = "⏸️ (spauzowane)" if self.is_paused else "▶️ Gra"
        loop_txt = " | 🔁 Pętla włączona" if self.loop else ""

        embed = discord.Embed(
            title="🎵 Teraz gra",
            description=f"**[{self.current.title}]({self.current.webpage_url})**",
            color=0x2ECC71
        )
        embed.add_field(name="Status", value=status + loop_txt, inline=False)
        embed.add_field(name="⏱️ Długość", value=self.current.duration_formatted, inline=True)
        embed.add_field(name="👤 Dodał", value=self.current.requester.mention, inline=True)
        await ctx.send(embed=embed)

    @commands.command(aliases=['vol'])
    async def volume(self, ctx: commands.Context, value: Optional[int] = None):
        """🔊 Ustawia głośność (0-100)"""
        if value is None:
            await ctx.send(f"🔊 Aktualna głośność: **{int(self.volume * 100)}%**")
            return

        if not 0 <= value <= 100:
            await ctx.send("❌ Głośność musi być między **0** a **100**!")
            return

        self.volume = value / 100
        if self.voice_client and self.voice_client.source:
            self.voice_client.source.volume = self.volume

        await ctx.send(f"🔊 Ustawiono głośność na **{value}%**")

    @commands.command(aliases=['disconnect', 'dc'])
    async def leave(self, ctx: commands.Context):
        """👋 Wychodzi z kanału głosowego"""
        vc = self._get_vc(ctx)
        if not vc:
            await ctx.send("❌ Nie jestem na kanale głosowym!")
            return

        self.queue.clear()
        self.current = None
        self.is_playing = False
        self.is_paused = False
        self.loop = False

        if vc.is_playing():
            vc.stop()

        await vc.disconnect()
        self.voice_client = None
        await ctx.send("👋 Wyszedłem z kanału")

    @commands.command()
    async def clear(self, ctx: commands.Context):
        """🗑️ Czyści kolejkę"""
        if not self.queue:
            await ctx.send("❌ Kolejka jest już pusta!")
            return

        count = len(self.queue)
        self.queue.clear()
        await ctx.send(f"🗑️ Wyczyszczono kolejkę (**{count}** utworów)")

    @commands.command(name='loop')
    async def loop_cmd(self, ctx: commands.Context):
        """🔁 Włącza/wyłącza pętlę"""
        if not self.current:
            await ctx.send("❌ Nic teraz nie gra!")
            return

        self.loop = not self.loop
        status = "włączona 🔁" if self.loop else "wyłączona"
        await ctx.send(f"🔁 Pętla **{status}**")

    @commands.command(aliases=['mix'])
    async def shuffle(self, ctx: commands.Context):
        """🔀 Miesza kolejkę"""
        if len(self.queue) < 2:
            await ctx.send("❌ W kolejce muszą być co najmniej **2** utwory!")
            return

        import random
        random.shuffle(self.queue)
        await ctx.send("🔀 Kolejka wymieszana!")

    # --------------------------------------------------------
    # ERROR HANDLERY
    # --------------------------------------------------------
    @play.error
    async def play_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("❌ Podaj tytuł lub link! Użycie: `$play <tytuł>`")
        else:
            await ctx.send(f"❌ Błąd: `{error}`")

    @volume.error
    async def volume_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send("❌ Podaj poprawną liczbę (0-100)!")

    # --------------------------------------------------------
    # AUTO-DISCONNECT
    # --------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        if not self.voice_client or member.id == self.bot.user.id:
            return

        if (before.channel == self.voice_client.channel
                and after.channel != self.voice_client.channel):
            if len(self.voice_client.channel.members) == 1:
                await asyncio.sleep(120)
                if self.voice_client and len(self.voice_client.channel.members) == 1:
                    self.queue.clear()
                    self.current = None
                    self.is_playing = False
                    self.is_paused = False

                    if self.voice_client.is_playing():
                        self.voice_client.stop()

                    try:
                        ch = self.voice_client.channel
                        await self.voice_client.disconnect()
                        self.voice_client = None
                        await ch.send("👋 Wyszedłem (brak osób przez 2 min)")
                    except Exception:
                        self.voice_client = None

# ============================================================
# SETUP
# ============================================================
async def setup(bot: commands.Bot):
    load_opus_lib()
    await bot.add_cog(Music(bot))
    print("🎵 Cog muzyczny załadowany!")