import collections
import contextlib
import io
import os
import shutil
import subprocess
import textwrap
import pathlib
import time
from datetime import timedelta

import psutil
import logging
import asyncio

import config

import nio
import humanize
import niobot
from bs4 import BeautifulSoup
from niobot import Context, NioBotException, FileAttachment

os.chdir(pathlib.Path(__file__).parent.absolute())

logging.basicConfig(level=getattr(config, "LOG_LEVEL", logging.INFO))
logging.getLogger("peewee").setLevel(logging.INFO)
logging.getLogger("nio.rooms").setLevel(logging.WARNING)


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
bot.queue = BackgroundQueue()
bot.ping_history = collections.deque(maxlen=100)
bot.mount_module("modules.ytdl")
bot.mount_module("modules.quote")


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
        members = await bot.joined_members(room_id)
        if isinstance(members, niobot.JoinedMembersResponse):
            if bot.user_id not in members.members:
                continue
            if len(members.members) == 1 and members.members[0] == bot.user_id:
                print("Leaving empty room:", room_id)
                bot.queue.add(bot.room_leave(room_id))


@bot.on_event("command_error")
async def on_command_error(ctx: Context, error: Exception):
    if isinstance(error, niobot.CommandArgumentsError):
        await ctx.respond("Invalid arguments: " + str(error))
    elif isinstance(error, niobot.CommandDisabledError):
        await ctx.respond("Command disabled: " + str(error))
    else:
        await ctx.respond("Error: " + str(error))


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


@bot.command(arguments=[niobot.Argument("room", str, default=None)])
async def leave(ctx: Context, room: str = None):
    """Leaves a room"""
    if not bot.is_owner(ctx.message.sender):
        return await ctx.respond("You are not my owner!")
    if room is None:
        room = ctx.room.room_id
    
    if room == "--empty":
        msg = await ctx.respond("Gathering rooms...")
        log = io.BytesIO()
        targets = []
        for room in bot.rooms.values():
            members = room.users.copy()
            if bot.user_id not in members:
                log.write(
                    b'Room %s (%s) is in the room list, but I am not a member?' % (
                        room.room_id,
                        room.display_name,
                    )
                )
                continue
            members.pop(bot.user_id, None)
            log.write(
                b'Room %s (%s) had %d members after popping myself.\n' % (
                    room.room_id,
                    room.display_name,
                    len(members),
                )
            )
            if len(members) == 0:
                targets.append(room.room_id)
        log.seek(0)
        await msg.edit('Leaving %d rooms...' % len(targets))
        for room in targets:
            await bot.room_leave(room)
            await bot.room_forget(room)
        
        value = log.getvalue()
        if len(value) > 1000:
            log.seek(0)
            await msg.delete()
            await ctx.respond(file=await niobot.FileAttachment.from_file(log, "leave.log"))
        else:
            await msg.edit('Done! Log:\n```%s```' % log.read().decode("utf-8"))

    msg = await ctx.respond("Leaving room %s" % room)
    response = await bot.room_leave(room)
    if isinstance(response, niobot.RoomLeaveError):
        await msg.edit("Failed to leave room %s: %s" % (room, response.message))
    else:
        await msg.edit("Left room %s" % room)


@bot.command(name="members")
async def members(ctx: Context, room_id: str = None, cached: int = 1):
    """Lists members of a given room"""
    if room_id is None:
        room_id = ctx.room.room_id
    
    room = bot.rooms.get(room_id)
    if room is None:
        return await ctx.respond("I am not in room %s." % room_id)
    
    if cached:
        members = room.users.copy()
    else:
        members = (await bot.joined_members(room_id)).members
        members = {x.id: x for x in members}
    
    if len(members) == 0:
        return await ctx.respond("Room %s has no members." % room_id)
    elif len(members) == 1:
        if list(members.keys())[0] == bot.user_id:
            return await ctx.respond("I am the only member of room %s." % room_id)
    
    if not bot.is_owner(ctx.message.sender):
        if ctx.message.sender not in members:
            return await ctx.respond("You do not have permission to view %r's members." % room_id)
    
    if len(members) > 10:
        x = io.BytesIO()
        x.write('{:,} members:\n'.format(len(members)).encode("utf-8"))
        for item in enumerate(members.values(), 1):
            x.write(('{0:,}. {1.display_name} ({1.user_id})\n'.format(*item)).encode('utf-8'))
        x.seek(0)
        return await ctx.respond(file=await niobot.FileAttachment.from_file(x, "members.txt"))
    else:
        return await ctx.respond(
            '```%s```' % '\n'.join(
                '{0:,}. {1.display_name} ({1.user_id})'.format(*item) for item in enumerate(members.values(), 1)
            )
        )

@bot.command(arguments=None)
# @bot.command(arguments=[niobot.Argument("room", str, default=None)])
async def join(ctx: Context, room: str = None):
    """Joins a room"""
    if not bot.is_owner(ctx.message.sender):
        return await ctx.respond("You are not my owner!")
    if room is None:
        room = ctx.room.room_id
    msg = await ctx.respond("Joining room %s" % room)
    response = await bot.join(room)
    if isinstance(response, niobot.JoinError):
        await msg.edit("Failed to join room %s: %s" % (room, response.message))
    else:
        await msg.edit("Joined room %s" % room)


@bot.command(arguments=[niobot.Argument("text", str), niobot.Argument("room", str, default=None)])
async def send(ctx: Context, text: str, room: str = None):
    """Sends a message to a room as this user"""
    if not bot.is_owner(ctx.message.sender):
        return await ctx.respond("You are not my owner!")
    if room is None:
        room = ctx.room.room_id
    msg = await ctx.respond("Sending message to room %s" % room)
    try:
        response = await bot.send_message(room, text, message_type="m.text")
    except niobot.MessageException as e:
        await msg.edit("Failed to send message to room %s: %s" % (room, e.message))
    else:
        await msg.edit("Sent message to room %s" % room)


bot.mount_module("modules.user_eval")
bot.run(access_token=getattr(config, "TOKEN", None), password=getattr(config, "PASSWORD", None))
