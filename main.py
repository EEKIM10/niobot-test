import collections
import os
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
from niobot import Context, NioBotException, MediaAttachment

logging.basicConfig(level=getattr(config, "LOG_LEVEL", logging.INFO))
logging.getLogger("peewee").setLevel(logging.INFO)


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
async def on_ready(_):
    bot.queue.start_worker()
    print("Logged in as %r!" % bot.user_id)
    print("Access token: %s" % bot.access_token)


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


@bot.command()
async def cud(ctx: Context):
    """Creates, updates, and deletes a message"""
    msg = await ctx.client.send_message(ctx.room, "Hello, World!")
    await asyncio.sleep(1)
    try:
        await ctx.client.edit_message(ctx.room, msg.event_id, "Goodbye, World!")
    except NioBotException as e:
        await ctx.respond(f"Failed to edit message: {e!r}")
    await asyncio.sleep(1)
    try:
        await ctx.client.delete_message(ctx.room, msg.event_id)
    except NioBotException as e:
        await ctx.respond(f"Failed to delete message: {e!r}")


@bot.command()
async def echo(ctx: Context):
    """Echos back your arguments"""
    content = ctx.args
    content = [x.replace("@", "@\u200b") for x in content]
    await ctx.respond("Your arguments: %s" % ', '.join(repr(x) for x in content))


@bot.command(name="upload-image")
async def upload_image(ctx: Context):
    """Uploads an image"""
    try:
        await ctx.respond("image.jpg", file=await MediaAttachment.from_file('./image.jpg'))
    except NioBotException as e:
        await ctx.respond("Failed to upload image: %r" % e)


@bot.command()
async def hello(ctx: Context):
    """Asks for an input"""
    res = await ctx.respond("Hello, what is your name?")
    try:
        _, msg = await bot.wait_for_message(sender=ctx.message.sender, room_id=ctx.room.room_id, timeout=10)
    except asyncio.TimeoutError:
        await res.edit("You took too long to respond!")
    else:
        await res.edit(f"Hello, {msg.body}!")


bot.mount_module("module_test")
bot.run(access_token=getattr(config, "TOKEN", None), password=getattr(config, "PASSWORD", None))
