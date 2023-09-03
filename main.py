import collections
import io
import os
import sys
import shutil
import subprocess
import pathlib
import time
import httpx
from datetime import timedelta
from pathlib import Path

import psutil
import logging
import asyncio

import config

import nio
import humanize
import niobot
from bs4 import BeautifulSoup
from niobot import Context, NioBotException
import help_command

os.chdir(pathlib.Path(__file__).parent.absolute())
sys.path.append("./venv/bin")
logging.basicConfig(level=getattr(config, "LOG_LEVEL", logging.INFO))
logging.getLogger("peewee").setLevel(logging.WARNING)
logging.getLogger("nio.rooms").setLevel(logging.ERROR)
logging.getLogger("nio.responses").setLevel(logging.ERROR)
logging.getLogger("nio.crypto.log").setLevel(logging.ERROR)
logging.getLogger("PIL.Image").setLevel(logging.ERROR)

MODULES = (
    "modules.quote",
    "modules.support",
    "modules.user_eval",
    "modules.ytdl",
    "modules.management"
)


class BackgroundQueue:
    """Handles deferring tasks (such as cleanup) to a queue.

    This queue system works in a FIFO system, and does not delay between task jobs.
    This is deal for tasks that need to be done in the background, but not necessarily immediately, or are maybe
    important however are async and do not have an async context.

    Or maybe just some simple tasks are thrown in here, who knows, who cares. If its in here, it'll get run."""
    def __init__(self):
        self.queue = asyncio.Queue(getattr(config, "QUEUE_SIZE", 100))
        self.log = logging.getLogger("bot_queue")
        self.task: asyncio.Task | None = None

    def start_worker(self):
        self.task = asyncio.create_task(self.worker())

    def add(self, task):
        self.queue.put_nowait(task)

    @property
    def healthy(self) -> bool:
        """Indicates the health of the queue worker. False means the task is not running."""
        if not self.task:
            return False
        running = not self.task.done() and not self.task.cancelled()
        return running

    async def worker(self):
        while True:
            task = await self.queue.get()
            try:
                task_id = id(task)
            except SystemError as e:
                self.log.critical("Failed to generate a unique ID for task %r", task)
                task_id = os.urandom(6).hex()
            self.log.debug("Running task %r", task_id)
            try:
                await niobot.run_blocking(task)
            except Exception as e:
                self.log.error("Failed to execute job %r: %r", task_id, e, exc_info=e)
            finally:
                self.log.debug("Finished task %r", task_id)
                self.queue.task_done()


bot = niobot.NioBot(
    getattr(config, "HOMESERVER", "https://matrix.nexy7574.co.uk"),
    getattr(config, "USER_ID", "@jimmy-bot:nexy7574.co.uk"),
    getattr(config, "DEVICE_ID", "nio-bot-test"),
    command_prefix=getattr(config, "COMMAND_PREFIX", "?"),
    owner_id=getattr(config, "OWNER_ID", "@nex:nexy7574.co.uk"),
    store_path=getattr(config, "STORE_PATH", "./store"),
)
bot.commands.pop('h')
bot.commands.pop('help')
bot.command('help', aliases=['h'])(help_command.custom_help)
bot.queue = BackgroundQueue()
bot.ping_history = collections.deque(maxlen=100)


async def kuma_ping_loop():
    session = httpx.AsyncClient(follow_redirects=True)
    while True:
        if len(bot.ping_history):
            _ping = round((sum(bot.ping_history) / len(bot.ping_history)), 2)
        else:
            _ping = ""
        try:
            await session.get(
                config.KUMA_URL.format(ping=_ping)
            )
            bot.log.debug("pinged kuma.")
        except Exception as e:
            bot.log.error("Failed to ping kuma: %r", e, exc_info=e)
        finally:
            await asyncio.sleep(60)


async def handle_key_verification_start(event: nio.KeyVerificationEvent):
    """Step 1"""
    if not isinstance(event, nio.KeyVerificationCancel):
        bot.log.warning("Declining verification from %r", event.sender)
        await bot.accept_key_verification(event.transaction_id)
        return await bot.cancel_key_verification(event.transaction_id)


# noinspection PyTypeChecker
bot.add_event_callback(handle_key_verification_start, (nio.KeyVerificationEvent,))


@bot.on_event("ready")
async def on_ready(_: niobot.SyncResponse):
    for module in MODULES:
        try:
            bot.mount_module(module)
        except Exception as e:
            logging.error("Failed to load %s: %s", module, e, exc_info=True)
    bot.queue.start_worker()
    try:
        from config import DISCORD_BRIDGE_TOKEN
    except ImportError:
        print("No loading discord bridge module, DISCORD_BRIDGE_TOKEN is not in config.py")
    else:
        bot.mount_module("modules.discord_bridge")
    print("Logged in as %r!" % bot.user_id)
    print("Prefix:", bot.command_prefix)
    print("Owner:", bot.owner_id)
    print("Device:", bot.device_id)
    for room_id, room in bot.rooms.items():
        _members = await bot.joined_members(room_id)
        if isinstance(_members, niobot.JoinedMembersResponse):
            if bot.user_id not in _members.members:
                continue
            if len(_members.members) == 1 and _members.members[0] == bot.user_id:
                print("Leaving empty room:", room_id)
                bot.queue.add(bot.room_leave(room_id))
    if hasattr(config, "KUMA_URL"):
        asyncio.create_task(kuma_ping_loop())


@bot.on_event("command_error")
async def on_command_error(ctx: Context, error: Exception):
    if isinstance(error, niobot.CommandArgumentsError):
        await ctx.respond("Invalid arguments: " + str(error))
    elif isinstance(error, niobot.CommandDisabledError):
        await ctx.respond("Command disabled: " + str(error))
    else:
        error = getattr(error, 'exception', error)
        await ctx.respond("Error: " + str(error))
        bot.log.error('command error in %s: %r', ctx.command.name, error, exc_info=error)


@bot.on_event("message")
async def on_message(_, event: nio.RoomMessageText):
    if bot.is_old(event):
        return
    latency = bot.latency(event)
    bot.ping_history.append(latency)


@bot.command()
async def ping(ctx: Context):
    """Shows the roundtrip latency"""
    latency = ctx.latency
    average = sum(bot.ping_history) / len(bot.ping_history)
    start = time.time()
    msg = await ctx.respond(f"Pong! {latency:.2f}ms (Average {average:.2f}ms)")
    end = time.time()
    await msg.edit(
        content=f"Pong! {latency:.2f}ms (Average {average:.2f}ms) (Reply time: {end - start:.2f}ms)"
    )


@bot.command()
async def info(ctx: Context):
    """Shows information about the bot"""
    def psutil_crap() -> dict:
        output = {}
        proc = psutil.Process()
        with proc.oneshot():
            output["memory_process"] = humanize.naturalsize(proc.memory_info().rss)
            output["cpu_process"] = proc.cpu_percent(interval=1)
            output["memory_system"] = humanize.naturalsize(psutil.virtual_memory().used)
            output["memory_pct"] = psutil.virtual_memory().percent
            output["cpu_system"] = psutil.cpu_percent(interval=1)
            output["uptime"] = time.time() - psutil.boot_time()
        return output

    uptime = time.time() - bot.start_time
    average_ping_seconds = sum(bot.ping_history) / len(bot.ping_history) / 1000
    psutil_data = await niobot.run_blocking(psutil_crap)
    table = {
        "Uptime": "%s (%s system)" % (humanize.naturaldelta(uptime), humanize.naturaldelta(psutil_data["uptime"])),
        "CPU Usage": "{0[cpu_process]:.1f}% ({0[cpu_system]:.1f}% system)".format(psutil_data),
        "Memory Usage": "{0[memory_process]} ({0[memory_system]} system, {0[memory_pct]}%)".format(psutil_data),
        "Ping": f"{ctx.latency:.2f}ms",
        "Average Ping": humanize.naturaltime(
            timedelta(seconds=average_ping_seconds), minimum_unit="microseconds"
        )[:-4],
    }
    if bot.queue.healthy:
        table["Queue"] = "<span data-mx-color=\"#00FF00\">Healthy</span>"
    else:
        table["Queue"] = "<span data-mx-color=\"#FF0000\">Dead!</span>"
    table_html = "<table><thead><tr>{head}</tr></thead><tbody><tr>{body}</tr></tbody></table>"
    head = ["<th>{}</th>".format(x) for x in table.keys()]
    body = ["<td>{}</td>".format(x) for x in table.values()]
    table_html = table_html.format(head="\n".join(head), body="\n".join(body))
    soup = BeautifulSoup(table_html, "html.parser")
    await ctx.respond("**Owner:** %s\n\n**Live device ID:** %s\n\n%s" % (bot.owner_id, bot.device_id, soup.prettify()))


@bot.command(name="upload", usage="<type: image|video|audio|file>", arguments=[niobot.Argument("type", str)])
async def upload_attachment(ctx: Context, _type: str):
    """Uploads an image"""
    msg = await ctx.respond("Processing media...")
    attachment = None
    if _type.startswith("/"):
        p = Path(__file__).parent / "assets" / _type[1:]
        if not p.exists():
            await msg.edit("File does not exist.")
            return
        # Make sure it doesn't go above __file__.parent:
        p = p.resolve()
        if (Path(__file__).parent / "assets") not in p.parents:
            await msg.edit("File does not exist (out of bounds).")
            return
        file_type = niobot.detect_mime_type(p)
        _types = {
            "image": niobot.ImageAttachment,
            "video": niobot.VideoAttachment,
            "audio": niobot.AudioAttachment
        }
        try:
            attachment = await _types.get(file_type.split("/")[0], niobot.FileAttachment).from_file(p)
        except Exception as e:
            await msg.edit(f"Failed to upload attachment: {e!r}")
            return
    else:
        try:
            match _type:
                case "image":
                    attachment = await niobot.ImageAttachment.from_file('./assets/image.jpg')
                case "video":
                    attachment = await niobot.VideoAttachment.from_file('./assets/bee-movie.webm')
                case "audio":
                    attachment = await niobot.AudioAttachment.from_file('./assets/zombo_words.mp3')
                case "file":
                    attachment = await niobot.FileAttachment.from_file('./assets/Manifesto.pdf')
                case _:
                    pass
        except Exception as e:
            await msg.edit(f"Failed to upload attachment: {e!r}")
            return
    if attachment is None:
        await msg.edit("Invalid attachment type. Please pick one of image, video, audio, or file.")
        return
    await msg.edit("Uploading attachment...")
    try:
        await ctx.respond(file=attachment)
    except NioBotException as e:
        await msg.edit(f"Failed to upload attachment: {e!r}")
        return
    await msg.edit("Attachment uploaded!")
    await asyncio.sleep(5)
    await msg.delete()


@bot.command()
async def version(ctx: Context, simple: bool = False):
    """Shows the version of nio"""
    if not simple and shutil.which("niocli"):
        result = await niobot.run_blocking(
            subprocess.run,
            ("niocli", "version"),
            text=True,
            capture_output=True,
        )
        await ctx.respond("```\n%s\n```" % result.stdout.strip())
    else:
        try:
            from niobot import __version__ as ver
        except ImportError:
            await ctx.respond("`niocli` is not installed (Version too old? PATH issue?)\n"
                              "Might be an ancient build, there's no \\_\\_version\\_\\_ either.")
        else:
            URL = "https://github.com/EEKIM10/nio-bot"
            await ctx.respond(
                "Running [nio-bot]({0}) version [{2}]({0}/tree/{1}).".format(
                    URL,
                    ver.__version_tuple__[-1].split(".")[0][1:],
                    ver.__version__,
                ),
            )


@bot.command(name="pretty-print", aliases=['pp'], arguments=[niobot.Argument("code", str, parser=niobot.json_parser)])
async def pretty_print(ctx: Context, code: str):
    """Pretty prints given JSON"""
    import json
    try:
        code = json.dumps(code, indent=4)
    except json.JSONDecodeError:
        pass
    if code.count("\n") > 35:
        x = io.BytesIO(code.encode("utf-8"))
        return await ctx.respond(file=await niobot.FileAttachment.from_file(x, "pretty-print.json"))
    await bot.add_reaction(ctx.room, ctx.message, "\N{white heavy check mark}")
    return await ctx.respond("```json\n%s\n```" % code)


@bot.command()
@niobot.is_owner()
async def send(ctx: Context, room: nio.MatrixRoom, text: str):
    """Sends a message to a room as this user"""
    if room is None:
        room = ctx.room.room_id
    msg = await ctx.respond("Sending message to room %s" % room)
    try:
        await bot.send_message(room, text, message_type="m.text")
    except niobot.MessageException as e:
        await msg.edit("Failed to send message to room %s: %s" % (room, e.message))
    else:
        await msg.edit("Sent message to room %s" % room)


@bot.command()
async def modules(ctx):
    """Lists currently loaded modules."""
    await ctx.respond("Loaded modules:\n%s" % "\n".join("* " + str(x) for x in MODULES))

bot.run(access_token=getattr(config, "TOKEN", None), password=getattr(config, "PASSWORD", None))
