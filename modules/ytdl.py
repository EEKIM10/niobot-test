import json
import pathlib
import asyncio
from urllib.parse import urlparse

import aiohttp
import niobot
import subprocess
import config
from functools import partial

import nio
import aiofiles
import magic
from yt_dlp import YoutubeDL
import tempfile
import typing

YTDL_ARGS: typing.Dict[str, typing.Any] = {
    "outtmpl": "%(title).50s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "no_warnings": True,
    "quiet": True,
    'noprogress': True,
    "nooverwrites": True,
    'format': "(bv+ba/b)[filesize<100M]/b"
}


class YoutubeDownloadModule(niobot.Module):
    def __init__(self, *args):
        super().__init__(*args)
        self.to_mount = {
            "ytdl": self.ytdl,
        }
        self.lock = asyncio.Lock()

    def _download(self, url: str, download_format: str, *, temp_dir: str) -> typing.List[pathlib.Path]:
        args = YTDL_ARGS.copy()
        dl_loc = pathlib.Path(temp_dir) / "dl"
        tmp_loc = pathlib.Path(temp_dir) / "tmp"
        dl_loc.mkdir(parents=True, exist_ok=True)
        tmp_loc.mkdir(parents=True, exist_ok=True)
        args["paths"] = {
            "temp": str(tmp_loc),
            "home": str(dl_loc),
        }
        if download_format:
            args["format"] = download_format
        else:
            args["format"] = "(bv+ba/b)[filesize<100M]"

        with YoutubeDL(args) as ytdl_instance:
            self.log.info("Downloading %s with format: %r", url, args["format"])
            ytdl_instance.download(
                [url]
            )

        x = list(dl_loc.iterdir())
        return x

    def get_metadata(self, file: pathlib.Path):
        _meta = subprocess.run(
            [
                "ffprobe",
                "-of",
                "json",
                "-loglevel",
                "9",
                "-show_entries",
                "stream=width,height",
                str(file)
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace"
        )
        if _meta.returncode != 0:
            self.log.warning("ffprobe failed (%d): %s", _meta.returncode, _meta.stderr)
            return
        return json.loads(_meta.stdout)

    async def upload_files(self, file: pathlib.Path):
        stat = file.stat()
        # max 99Mb
        if stat.st_size > 99 * 1024 * 1024:
            self.log.warning("File %s is too big (%d bytes)", file, stat.st_size)
            return
        mime = magic.Magic(mime=True).from_file(file)
        self.log.debug("File %s is %s", file, mime)
        metadata = self.get_metadata(file) or {}
        if not metadata.get("streams"):
            self.log.warning("No streams for %s", file)
            return
        if not metadata["streams"][0].get("width"):
            self.log.warning("No width for %s", file)
            return
        if not metadata["streams"][0].get("height"):
            self.log.warning("No height for %s", file)
            return

        body = {
            "body": file.name,
            "info": {
                "mimetype": mime,
                "h": int(metadata["streams"][0]["height"]),
                "w": int(metadata["streams"][0]["width"]),
                "size": stat.st_size,
            },
            "msgtype": "m." + mime.split("/")[0],
        }
        async with aiofiles.open(file, "r+b") as _file:
            size_mb = stat.st_size / 1024 / 1024
            self.log.info("Uploading %s (%dMb)", file, size_mb)
            response, keys = await self.client.upload(
                _file,
                content_type=mime,
                filename=file.name,
                filesize=stat.st_size
            )
            self.log.info("Uploaded %s", file)
            self.log.debug("%r (%r)", response, keys)
        if isinstance(response, nio.UploadResponse):
            body["url"] = response.content_uri
            return body

    async def get_video_info(self, url: str, secure: bool = False) -> dict:
        """Extracts JSON information about the video"""
        args = YTDL_ARGS.copy()
        with YoutubeDL(args) as ytdl_instance:
            info = ytdl_instance.extract_info(url, download=False)
            info = ytdl_instance.sanitize_info(info, remove_private_keys=secure)
        self.log.debug("ytdl info for %s: %r", url, info)
        return info

    @staticmethod
    def resolve_thumbnail(info: dict, resolution: str = None) -> typing.Optional[str]:
        """Resolves the thumbnail URL from the info dict"""
        width, height = 0, 0
        if resolution:
            width, height = map(int, resolution.split("x"))
        if info.get("thumbnails"):
            if isinstance(info["thumbnails"], list):
                thumbs = info["thumbnails"].copy()
                thumbs.sort(key=lambda x: x.get("preference", 0), reverse=True)
                if width and height:
                    thumbs.sort(key=lambda x: abs(x["width"] - width) + abs(x["height"] - height))
                return thumbs[0]["url"]
        if info.get("thumbnail"):
            return info["thumbnail"]

    @niobot.command(
        "ytdl",
        help="Downloads a video from YouTube", 
        aliases=['yt', 'dl', 'yl-dl', 'yt-dlp'], 
        usage="<url> [format]",
        arguments=[
            niobot.commands.Argument(
                "url",
                str,
                description="The URL to download.",
                required=True,
            ),
            niobot.commands.Argument(
                "_format",
                str,
                description="The format to download in.",
                required=False,
                default="(bv+ba/b)[filesize<80M]/b"
            ),
        ]
    )
    async def ytdl(self, ctx: niobot.Context, url: str, _format: str = None):
        """Downloads a video from YouTube"""
        if self.lock.locked():
            msg = await ctx.respond("Waiting for previous download to finish...")
        else:
            msg = await ctx.respond("Downloading...")
        async with self.lock:
            room = ctx.room
            dl_format = _format or "(bv+ba/b)[filesize<=80M]/b"  #
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    info = await self.get_video_info(url)
                    if not info:
                        await msg.edit("Could not get video info (Restricted?)")
                        return
                    size = int(info.get("filesize") or info.get("filesize_approx") or 30 * 1024 * 1024)
                    download_speed = getattr(config, "DOWNLOAD_SPEED_BITS", 75)
                    ETA = (size * 8) / download_speed
                    minutes, seconds = divmod(ETA, 60)
                    seconds = round(seconds)
                    await msg.edit(
                        "Downloading [%r](%s) (ETA %s)..." % (
                            info["title"],
                            info["original_url"],
                            "%d minutes and %d seconds" % (minutes, seconds) if minutes else "%d seconds" % seconds
                        )
                    )
                    self.log.info("Downloading %s to %s", url, temp_dir)
                    files = await niobot.run_blocking(self._download, url, dl_format, temp_dir=temp_dir)
                    self.log.info("Downloaded %d files", len(files))
                    if not files:
                        await msg.edit("No files downloaded")
                        return
                    sent = False
                    for file in files:
                        data = await niobot.run_blocking(
                            niobot.get_metadata,
                            file
                        )
                        size_mb = file.stat().st_size / 1024 / 1024
                        resolution = "%dx%d" % (data["streams"][0]["width"], data["streams"][0]["height"])

                        thumbnail_url = self.resolve_thumbnail(info, resolution)
                        if thumbnail_url:
                            parsed = urlparse(thumbnail_url)
                            with tempfile.NamedTemporaryFile(
                                suffix='.%s' % parsed.path.split(".")[-1],
                                delete=False
                            ) as thumb:
                                async with aiohttp.ClientSession(
                                    headers={
                                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
                                                      niobot.__user_agent__
                                    }
                                ) as session:
                                    async with session.get(thumbnail_url) as resp:
                                        if resp.status == 200:
                                            thumb.write(await resp.read())
                                            thumb.flush()
                                            thumb.seek(0)
                                            att = await niobot.ImageAttachment.from_file(
                                                thumb.name,
                                                file_name=parsed.path.split("/")[-1]
                                            )
                                            await att.upload(ctx.client)
                                            thumbnail = att

                        upload_speed = getattr(config, "UPLOAD_SPEED_BITS", 15)
                        ETA = (size_mb * 8) / upload_speed
                        minutes, seconds = divmod(ETA, 60)
                        seconds = round(seconds)
                        await msg.edit(
                            "Uploading %s (%dMb, %s, ETA %s)..." % (
                                file.name,
                                size_mb,
                                resolution,
                                "%d minutes and %d seconds" % (minutes, seconds) if minutes else "%d seconds" % seconds
                            )
                        )
                        self.log.info("Uploading %s (%dMb, %s)", file.name, size_mb, resolution)
                        upload = await niobot.VideoAttachment.from_file(
                            file,
                            thumbnail=thumbnail,
                        )
                        upload.thumbnail.info["h"] = upload.info["h"]
                        upload.thumbnail.info["w"] = upload.info["w"]
                        try:
                            await self.client.send_message(room, content=file.name, file=upload)
                        except Exception as e:
                            self.log.error("Error: %s", e, exc_info=e)
                            await msg.edit("Error: %r" % e)
                            return
                        sent = True

                    if sent:
                        await msg.edit("Completed, downloaded [your video]({})".format("url"))
                        await asyncio.sleep(10)
                        await msg.delete("Command completed.")
            except Exception as e:
                self.log.error("Error: %s", e, exc_info=e)
                await msg.edit("Error: " + str(e))
                return

    @niobot.command("ytdl-metadata", arguments=[niobot.Argument("url", str, description="The URL to download.")])
    async def ytdl_metadata(self, ctx: niobot.Context, url: str):
        """Downloads and exports a JSON file with the metadata for the given video."""
        msg = await ctx.respond("Downloading...")
        extracted = await self.get_video_info(url, secure=True)
        if not extracted:
            await msg.edit("Could not get video info (Restricted?)")
            return
        pretty = json.dumps(extracted, indent=4, default=repr)
        if len(pretty) < 2000:
            await msg.edit("```json\n%s\n```" % pretty)
            return

        with tempfile.NamedTemporaryFile(suffix=".json") as temp_file:
            with open(temp_file.name, "w") as __temp_file:
                json.dump(extracted, __temp_file, indent=4, default=repr)
                __temp_file.flush()
            upload = niobot.FileAttachment(temp_file.name, "application/json")
            await ctx.respond("info.json", file=upload)
            await msg.delete()
