import asyncio
import collections
import datetime
import json
import websockets
import aiohttp
import niobot
from nio import MatrixRoom, RoomMessageText, RoomMessageMedia
import pathlib
import tempfile

try:
    from config import DISCORD_BRIDGE_TOKEN
except ImportError:
    DISCORD_BRIDGE_TOKEN = None


class QuoteModule(niobot.Module):
    def __init__(self, bot: niobot.NioBot):
        super().__init__(bot)
        self.bot.add_event_callback(self.on_message, (RoomMessageText, RoomMessageMedia))
        self.fifo_task = asyncio.create_task(self.message_poller())
        self.last_author: str = "@jimmy-bot:nexy7574.co.uk"
        self.last_author_ts = 0
        self.bridge_responses = collections.deque(maxlen=100)
        self.bridge_lock = asyncio.Lock()

    async def message_poller(self):
        if not DISCORD_BRIDGE_TOKEN:
            return
        ROOM_ID = "!WrLNqENUnEZvLJiHsu:nexy7574.co.uk"
        room = self.bot.rooms[ROOM_ID]
        while True:
            try:
                async with aiohttp.ClientSession(headers={"User-Agent": niobot.__user_agent__}) as client:
                    self.log.info("Starting fifo task")
                    async for ws in websockets.connect(
                            "wss://droplet.nexy7574.co.uk/jimmy/bridge/recv",
                            extra_headers={"secret": DISCORD_BRIDGE_TOKEN}
                    ):
                        async for payload in ws:
                            self.log.debug("Decoding payload...")
                            payload = json.loads(payload)
                            self.log.debug("Received payload: %s", payload)
                            if payload["author"] == "Jimmy Savile#3762":
                                continue
                            _author = self.last_author
                            y = None
                            self.last_author = payload["author"]
                            if payload["content"]:
                                if _author == payload["author"]:
                                    text = "<blockquote>%s</blockquote>"
                                    args = (payload["content"],)
                                else:
                                    text = "**%s**:<br><blockquote>%s</blockquote>"
                                    args = (payload["author"], payload["content"])
                                y = await self.bot.send_message(
                                    room,
                                    text % args,
                                    message_type="m.text"
                                )
                                self.bridge_responses.append(y.event_id)

                            if payload["attachments"]:
                                for attachment in payload["attachments"]:
                                    try:
                                        async with client.get(attachment["url"]) as response:
                                            if response.status != 200:
                                                continue

                                            with tempfile.NamedTemporaryFile(
                                                    suffix=pathlib.Path(attachment["url"]).suffix
                                            ) as tmp:
                                                tmp.write(await response.read())
                                                tmp.flush()
                                                tmp.seek(0)
                                                if attachment["content_type"].startswith("image/"):
                                                    media_factory = niobot.ImageAttachment
                                                elif attachment["content_type"].startswith("video/"):
                                                    media_factory = niobot.VideoAttachment
                                                elif attachment["content_type"].startswith("audio/"):
                                                    media_factory = niobot.AudioAttachment
                                                else:
                                                    media_factory = niobot.FileAttachment
                                                media = await media_factory.from_file(
                                                    tmp.name,
                                                )
                                                x = await self.bot.send_message(
                                                    room,
                                                    'BRIDGE_' + attachment["filename"],
                                                    file=media,
                                                    reply_to=y.event_id
                                                )
                                                self.bridge_responses.append(x.event_id)
                                    except Exception as e:
                                        self.log.exception("Error while mirroring discord media: %r", e, exc_info=e)
                                        continue
            except Exception as e:
                self.log.exception("Error while reading from websocket: %r", e, exc_info=e)
                continue

    # @niobot.event("message")
    async def on_message(self, room: MatrixRoom, event: RoomMessageText | RoomMessageMedia):
        async with self.bridge_lock:
            self.log.debug("Processing message: %s in %s", event, room)
            if self.bot.is_old(event):
                self.log.debug("Ignoring old message: %s in %s", event, room)
                return

            if room.room_id != "!WrLNqENUnEZvLJiHsu:nexy7574.co.uk":
                self.log.debug("Ignoring message in %s", room)
                return

            if event.body.startswith(("~", "?", "!")):
                self.log.debug("Ignoring escaped message: %s", event)
                return

            if event.sender == self.bot.user_id and event.event_id in self.bridge_responses:
                self.log.debug("Ignoring message from self: %s", event)
                return

            self.bridge_responses.append(event.event_id)

            if DISCORD_BRIDGE_TOKEN:
                payload = {
                    "secret": DISCORD_BRIDGE_TOKEN,
                    "sender": event.sender,
                    "message": event.body
                }
                if isinstance(event, RoomMessageMedia):
                    payload["message"] = await self.bot.mxc_to_http(event.url)
                self.log.debug("Payload: %s", payload)
                async with aiohttp.ClientSession(headers={"User-Agent": niobot.__user_agent__}) as client:
                    self.log.debug("Sending message to discord bridge")
                    async with client.post(
                        "https://droplet.nexy7574.co.uk/jimmy/bridge",
                        json=payload,
                        headers={
                            "Connection": "Close"
                        },
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as response:
                        if response.status == 400:
                            data = await response.json()
                            if data["detail"] == "Message too long.":
                                await self.bot.add_reaction(room, event, "\N{PRINTER}\N{VARIATION SELECTOR-16}")
                        elif response.status != 201:
                            self.log.error(
                                "Error while sending message to discord bridge (%d): %s",
                                response.status,
                                await response.text()
                            )
                            await self.bot.add_reaction(room, event, "\N{CROSS MARK}")
                            return
                        self.log.info("Message sent to discord bridge")
            else:
                self.log.debug("No discord bridge token set, ignoring message")

    @niobot.command("bridge-status", hidden=True)
    @niobot.is_owner()
    async def bridge_status(self, ctx: niobot.Context):
        """Get the status of the discord bridge"""
        task_okay = self.fifo_task and not self.fifo_task.done()
        last_ts = datetime.datetime.fromtimestamp(self.last_author_ts, tz=datetime.timezone.utc)
        lines = [
            "WebSocket: %s" % ("Okay" if task_okay else "Not connected"),
            "Lock: %s" % ("Locked" if self.bridge_lock.locked() else "Not locked"),
            "Last author: `%s`" % self.last_author,
            "Last author timestamp: `%d` (%s)" % (
                self.last_author_ts,
                last_ts.strftime("%d/%m/%Y %H:%M:%S %Z")
            ),
        ]
        await ctx.respond("\n".join(lines))
