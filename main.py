import time
import logging
import asyncio

import config

import nio
import niobot
from niobot import Context, NioBotException, MediaAttachment

logging.basicConfig(level=logging.DEBUG)


bot = niobot.NioBot(
    "https://matrix.nexy7574.co.uk",
    "@jimmy-bot:nexy7574.co.uk",
    "nio-bot-test",
    command_prefix="?",
    owner_id="@nex:nexy7574.co.uk"
)


@bot.on_event("ready")
async def on_ready(first_sync_result: nio.SyncResponse):
    print("Logged in as %r!" % bot.user_id)


@bot.command()
async def ping(ctx: Context):
    """SHows the roundtrip latency"""
    logging.info("timestamp now is %s" % time.time())
    latency = time.time() - ctx.message.server_timestamp / 1000
    await ctx.reply(f"Pong! {latency:.2f}s")


@bot.command()
async def cud(ctx: Context):
    """Creates, updates, and deletes a message"""
    msg = await ctx.client.send_message(ctx.room, "Hello, World!")
    await asyncio.sleep(1)
    try:
        await ctx.client.edit_message(ctx.room, msg.event_id, "Goodbye, World!")
    except NioBotException as e:
        await ctx.reply(f"Failed to edit message: {e!r}")
    await asyncio.sleep(1)
    try:
        await ctx.client.delete_message(ctx.room, msg.event_id)
    except NioBotException as e:
        await ctx.reply(f"Failed to delete message: {e!r}")


@bot.command()
async def echo(ctx: Context):
    """Echos back your arguments"""
    content = ctx.args
    content = [x.replace("@", "@\u200b") for x in content]
    await ctx.reply("Your arguments: %s" % ', '.join(repr(x) for x in content))


@bot.command(name="upload-image")
async def upload_image(ctx: Context):
    """Uploads an image"""
    try:
        await ctx.reply("image.jpg", file=await MediaAttachment.from_file('./image.jpg'))
    except NioBotException as e:
        await ctx.reply("Failed to upload image: %r" % e)


@bot.command()
async def hello(ctx: Context):
    """Asks for an input"""
    res = await ctx.reply("Hello, what is your name?")
    try:
        _, msg = await bot.wait_for_message(sender=ctx.message.sender, room_id=ctx.room.room_id, timeout=10)
    except asyncio.TimeoutError:
        await bot.edit_message(ctx.room, res.event_id, "You took too long to respond!")
    else:
        await bot.edit_message(ctx.room, res.event_id, f"Hello, {msg.body}!")


bot.mount_module("module_test")
bot.run(access_token=config.TOKEN)
