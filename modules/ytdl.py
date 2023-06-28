import json
import pathlib
import asyncio
import niobot
import subprocess
from functools import partial
from niobot.commands import Argument

import nio
import aiofiles
import logging
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

    def _download(self, url: str, download_format: str, *, temp_dir: str) -> typing.List[pathlib.Path]:
        args = YTDL_ARGS.copy()
        args["paths"] = {
            "temp": temp_dir,
            "home": temp_dir
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

        return list(pathlib.Path(temp_dir).glob("*"))

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

    async def get_video_info(self, url: str) -> dict:
        """Extracts JSON information about the video"""
        args = YTDL_ARGS.copy()
        with YoutubeDL(args) as ytdl_instance:
            info = ytdl_instance.extract_info(url, download=False)
        self.log.debug("ytdl info for %s: %r", url, info)
        return info

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
    async def ytdl(self, ctx: niobot.Context, url: str = None, _format: str = None):
        """Downloads a video from YouTube"""
        args = ctx.args
        room = ctx.room
        if not args:
            await ctx.respond("Usage: ?ytdl <url> [format]")
            return

        args = args.copy()  # disown original
        url = url or args.pop(0)
        dl_format = _format or "(bv+ba/b)[filesize<80M]/b"  #
        if args:
            dl_format = _format or args.pop(0)

        msg = await ctx.respond("Downloading...")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                info = await self.get_video_info(url)
                if not info:
                    await msg.edit("Could not get video info (Restricted?)")
                    return
                await msg.edit("Downloading [%r](%s)..." % (info["title"], info["original_url"]))
                self.log.info("Downloading %s to %s", url, temp_dir)
                loop = asyncio.get_event_loop()
                files = await loop.run_in_executor(
                    None,
                    partial(self._download, url, dl_format, temp_dir=temp_dir)
                )
                self.log.info("Downloaded %d files", len(files))
                if not files:
                    await msg.edit("No files downloaded")
                    return
                sent = False
                for file in files:
                    data = self.get_metadata(file)
                    size_mb = file.stat().st_size / 1024 / 1024
                    resolution = "%dx%d" % (data["streams"][0]["width"], data["streams"][0]["height"])
                    await msg.edit("Uploading %s (%dMb, %s)..." % (file.name, size_mb, resolution))
                    self.log.info("Uploading %s (%dMb, %s)", file.name, size_mb, resolution)
                    upload = await niobot.MediaAttachment.from_file(
                        file,
                    )
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
