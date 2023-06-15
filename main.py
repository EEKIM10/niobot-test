import time
import logging
import asyncio

import config

import nio
import niobot
from niobot import Context, NioBotException, MediaAttachment

logging.basicConfig(level=getattr(config, "LOG_LEVEL", logging.INFO))
logging.getLogger("peewee").setLevel(logging.INFO)


bot = niobot.NioBot(
    getattr(config, "HOMESERVER", "https://matrix.nexy7574.co.uk"),
    getattr(config, "USER_ID", "@jimmy-bot:nexy7574.co.uk"),
    getattr(config, "DEVICE_ID", "nio-bot-test"),
    command_prefix=getattr(config, "COMMAND_PREFIX", "?"),
    owner_id=getattr(config, "OWNER_ID", "@nex:nexy7574.co.uk"),
    store_path=getattr(config, "STORE_PATH", "./store"),
)
bot.ping_history = []
bot.mount_module("modules.ytdl")
bot.mount_module("modules.quote")


@bot.on_event("ready")
async def on_ready(first_sync_result: nio.SyncResponse):
    print("Logged in as %r!" % bot.user_id)
    print("Access token: %s" % bot.access_token)


@bot.on_event("message")
async def on_message(room: nio.MatrixRoom, event: nio.RoomMessageText):
    latency = bot.latency(event)
    bot.ping_history.append(latency)


@bot.command()
async def ping(ctx: Context):
    """Shows the roundtrip latency"""
    latency = ctx.latency
    average = sum(bot.ping_history) / len(bot.ping_history)
    await ctx.respond(f"Pong! {latency:.2f}ms (Average {average:.2f}ms)")


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
