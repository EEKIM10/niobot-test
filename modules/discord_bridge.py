import asyncio
import collections
import datetime
import io
import json
import aiosqlite
import hashlib

import PIL.Image
import PIL.ImageDraw
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
        self.processing = {}

    async def get_mxc_for(self, avatar_url: str) -> str:
        loc = pathlib.Path.home() / ".cache" / "jimmy-matrix" / "avatars.db"
        loc.parent.mkdir(0o751, True, True)
        async with aiosqlite.connect(loc) as connection:
            await connection.execute("CREATE TABLE IF NOT EXISTS avatars (url TEXT PRIMARY KEY, mxc TEXT)")
            await connection.commit()
            async with connection.execute("SELECT mxc FROM avatars WHERE url = ?", (avatar_url,)) as cursor:
                row = await cursor.fetchone()
                if row is not None:
                    self.log.debug("Avatar %r is cached, returning %r", avatar_url, row[0])
                    return row[0]
            self.log.info("Avatar %r is not cached, uploading.", avatar_url)
            async with aiohttp.ClientSession(headers={"User-Agent": niobot.__user_agent__}) as client:
                async with client.get(avatar_url) as response:
                    response.raise_for_status()
                    content_type = response.headers["Content-Type"].split(";")[0].split("/")[1]
                    with tempfile.NamedTemporaryFile(suffix="." + content_type) as tmp:
                        tmp.write(await response.read())
                        tmp.flush()
                        await niobot.run_blocking(self.make_image_round, pathlib.Path(tmp.name))
                        tmp.seek(0)
                        media = await niobot.ImageAttachment.from_file(tmp.name)
                        await media.upload(self.bot, False)
                        await connection.execute("INSERT INTO avatars (url, mxc) VALUES (?, ?)",
                                                 (avatar_url, media.url))
                        await connection.commit()
                        return media.url

    @staticmethod
    def make_image_round(path: pathlib.Path) -> pathlib.Path:
        """Effectively the same as adding border-radius: 50% to the image"""
        img = PIL.Image.open(path)
        img = img.convert("RGBA")
        mask = PIL.Image.new("L", img.size, 0)

        draw = PIL.ImageDraw.Draw(mask)
        draw.ellipse((0, 0) + img.size, fill=255)

        img.putalpha(mask)
        img.thumbnail((16, 16), PIL.Image.Resampling.LANCZOS, 3)
        img.save(path)
        return path

    async def message_poller(self):
        if not DISCORD_BRIDGE_TOKEN:
            return
        ROOM_ID = "!WrLNqENUnEZvLJiHsu:nexy7574.co.uk"
        room = self.bot.rooms[ROOM_ID]
        while True:
            try:
                async with aiohttp.ClientSession(headers={"User-Agent": niobot.__user_agent__}) as client:
                    self.log.info("Starting discord bridge task")
                    async for ws in websockets.connect(
                            "wss://droplet.nexy7574.co.uk/jimmy/bridge/recv",
                            extra_headers={"secret": DISCORD_BRIDGE_TOKEN}
                    ):
                        self.log.info("Connected to discord bridge & awaiting messages.")
                        async for payload in ws:
                            payload_id = hashlib.md5(payload.encode("utf-8"), usedforsecurity=False).hexdigest()
                            self.processing[payload_id] = {
                                "state": "processing",
                                "since": datetime.datetime.now(datetime.timezone.utc)
                            }
                            self.log.debug("Decoding payload...")
                            try:
                                payload = json.loads(payload)
                            except json.JSONDecodeError as e:
                                self.log.exception("Error while decoding payload: %r", e, exc_info=e)
                                self.processing[payload_id] = {
                                    "state": "done",
                                    "success": False,
                                    "error": "JSONDecodeError",
                                    "since": datetime.datetime.now(datetime.timezone.utc),
                                    "history": self.processing[payload_id]
                                }
                                continue
                            self.log.info("Received bridge payload:\n%s", json.dumps(payload, indent=4))
                            if payload["author"] == "Jimmy Savile#3762":
                                self.processing[payload_id] = {
                                    "state": "done",
                                    "success": False,
                                    "error": "Ignored",
                                    "since": datetime.datetime.now(datetime.timezone.utc),
                                    "history": self.processing[payload_id]
                                }
                                continue
                            _author = self.last_author
                            y = None
                            self.last_author = payload["author"]
                            self.last_author_ts = payload["at"]
                            if payload["content"]:
                                # noinspection PyProtectedMember
                                pre_render = await self.bot._markdown_to_html(payload["content"])
                                if _author == payload["author"]:
                                    text = "<blockquote>%s</blockquote>"
                                    args = (pre_render,)
                                else:
                                    text = "**%s**:<br><blockquote>%s</blockquote>"
                                    if payload.get("avatar"):
                                        avatar_url = payload["avatar"]
                                        self.processing[payload_id] = {
                                            "state": "processing avatar",
                                            "since": datetime.datetime.now(datetime.timezone.utc),
                                            "history": self.processing[payload_id]
                                        }
                                        avatar_mxc = await self.get_mxc_for(avatar_url)
                                        _resolved_author = '<img src="%s" width="16px" height="16px"> %s' % (
                                            avatar_mxc,
                                            payload["author"]
                                        )
                                    else:
                                        _resolved_author = payload["author"]
                                    args = (_resolved_author, pre_render)
                                y = await self.bot.send_message(
                                    room,
                                    text % args,
                                    message_type="m.text"
                                )
                                self.bridge_responses.append(y.event_id)

                            if payload["attachments"]:
                                for attachment in payload["attachments"]:
                                    try:
                                        self.processing[payload_id] = {
                                            "state": "fetching attachment",
                                            "url": attachment["url"],
                                            "since": datetime.datetime.now(datetime.timezone.utc),
                                            "history": self.processing[payload_id]
                                        }
                                        async with client.get(attachment["url"]) as response:
                                            if response.status != 200:
                                                continue

                                            with tempfile.NamedTemporaryFile(
                                                    suffix=pathlib.Path(attachment["url"]).suffix
                                            ) as tmp:
                                                self.processing[payload_id] = {
                                                    "state": "downloading attachment",
                                                    "url": attachment["url"],
                                                    "file": tmp.name,
                                                    "since": datetime.datetime.now(datetime.timezone.utc),
                                                    "history": self.processing[payload_id]
                                                }
                                                tmp.write(await response.read())
                                                tmp.flush()
                                                tmp.seek(0)

                                                self.processing[payload_id] = {
                                                    "state": "processing attachment",
                                                    "url": attachment["url"],
                                                    "file": tmp.name,
                                                    "since": datetime.datetime.now(datetime.timezone.utc),
                                                    "history": self.processing[payload_id]
                                                }
                                                if attachment["content_type"].startswith("image/"):
                                                    media = await niobot.ImageAttachment.from_file(
                                                        tmp.name,
                                                        generate_blurhash=False
                                                    )
                                                    assert media.xyz_amorgan_blurhash is None
                                                    thumbnail = io.BytesIO()
                                                    (
                                                        await niobot.run_blocking(
                                                            media.thumbnailify_image,
                                                            PIL.Image.open(media.file)
                                                        )
                                                    ).save(thumbnail, "webp")
                                                    await media.get_blurhash(file=thumbnail)
                                                elif attachment["content_type"].startswith("video/"):
                                                    # step one - create the video attachment without a thumbnail
                                                    media = await niobot.VideoAttachment.from_file(
                                                        tmp.name,
                                                        generate_blurhash=False,
                                                        thumbnail=False
                                                    )

                                                    # step two - extract the first frame of the video
                                                    _frame_one = await niobot.run_blocking(
                                                        niobot.first_frame,
                                                        PIL.Image.open(media.file),
                                                        "webp"
                                                    )
                                                    frame_one = io.BytesIO()
                                                    _frame_one.save(frame_one, "webp")

                                                    # step three - scale the video down to 320x240
                                                    thumbnail = io.BytesIO(
                                                        await niobot.run_blocking(
                                                            niobot.ImageAttachment.thumbnailify_image,
                                                            frame_one
                                                        )
                                                    )

                                                    # Step four - cast to an image attachment
                                                    media_thumbnail = await niobot.ImageAttachment.from_file(
                                                        thumbnail,
                                                    )

                                                    # Step five - assign the thumbnail to the video attachment
                                                    media.thumbnail = media_thumbnail
                                                else:
                                                    factory = niobot.which(tmp.name)
                                                    if factory is None:
                                                        continue
                                                    media = await factory.from_file(tmp.name)

                                                self.processing[payload_id] = {
                                                    "state": "uploading attachment",
                                                    "url": attachment["url"],
                                                    "file": tmp.name,
                                                    "obj": media,
                                                    "since": datetime.datetime.now(datetime.timezone.utc),
                                                    "history": self.processing[payload_id]
                                                }
                                                x = await self.bot.send_message(
                                                    room,
                                                    'BRIDGE_' + attachment["filename"],
                                                    file=media,
                                                    reply_to=y.event_id if y else None
                                                )
                                                self.bridge_responses.append(x.event_id)
                                    except Exception as e:
                                        self.log.exception("Error while mirroring discord media: %r", e, exc_info=e)
                                        self.processing[payload_id] = {
                                            "state": "failed mirroring attachment attachment",
                                            "url": attachment["url"],
                                            "error": e,
                                            "success": False,
                                            "since": datetime.datetime.now(datetime.timezone.utc),
                                            "history": self.processing[payload_id]
                                        }
                                        continue
                            self.processing[payload_id] = {
                                "state": "done",
                                "success": True,
                                "since": datetime.datetime.now(datetime.timezone.utc),
                                "history": self.processing[payload_id]
                            }
            except Exception as e:
                self.log.exception("Error while reading from websocket: %r", e, exc_info=e)
                self.processing[payload_id] = {
                    "state": "websocket error",
                    "error": e,
                    "success": False,
                    "since": datetime.datetime.now(datetime.timezone.utc),
                    "history": self.processing[payload_id]
                }
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
            "* WebSocket: %s" % ("Okay" if task_okay else "Not connected"),
            "* Lock: %s" % ("Locked" if self.bridge_lock.locked() else "Not locked"),
            "* Last author: `%s`" % self.last_author,
            "* Last author timestamp: `%d` (%s)" % (
                self.last_author_ts,
                last_ts.strftime("%d/%m/%Y %H:%M:%S %Z")
            ),
        ]
        processing = []
        for key, data in self.processing.items():
            if data.get("success") in (True, False):
                continue
            processing.append(
                "* %s: %s, since %s" % (
                    key,
                    data["state"] + (" (%s)" % data["url"]) if data.get("url") else '',
                    data["since"].strftime("%d/%m/%Y %H:%M:%S %Z")
                )
            )
        if processing:
            lines.append("\n")
            lines.extend(processing)
        await ctx.respond("\n".join(lines))
