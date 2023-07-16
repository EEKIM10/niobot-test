# Support room module
import datetime
import logging
import packaging.version

import niobot
import niobot.__version__ as niobot_version
import httpx
import asyncio


class SupportRoomModule(niobot.Module):
    ROOM_ID = "!rwJEulKnHLoffvXAof:nexy7574.co.uk"
    PYPI_API_URL = "https://pypi.org/pypi/nio-bot/json"
    GITHUB_API_URL = "https://api.github.com/repos/EEKIM10/niobot/releases/latest"
    
    def __init__(self, bot: niobot.NioBot):
        super().__init__(bot)
        self.http_client = httpx.AsyncClient(
            headers={
                "user-agent": niobot.__user_agent__
            }
        )
        self.last_etag = None
        self.next_run = datetime.datetime.utcnow()
        self.log = logging.getLogger(__name__)
        self.task = asyncio.create_task(self.github_task())

    @staticmethod
    def version_is_newer(a: str, b: str) -> bool:
        """Checks if version A is newer than version B."""
        a = packaging.version.parse(a)
        b = packaging.version.parse(b)
        return a > b

    async def github_task(self):
        if hasattr(self.bot, "is_ready"):
            await self.bot.is_ready.wait()
        else:
            await asyncio.sleep(10)

        while True:
            try:
                headers = {
                    "Accept": "application/vnd.github+json",
                }
                if self.last_etag:
                    headers["If-None-Match"] = self.last_etag
                response = await self.http_client.get(
                    self.GITHUB_API_URL,
                    headers=headers
                )
                if response.status_code == 304:
                    data = None
                else:
                    response.raise_for_status()
                    self.last_etag = response.headers["etag"]
                    data = response.json()
            except httpx.HTTPStatusError as e:
                self.log.warning("Failed to fetch latest release data from GitHub: %s", e)
            except httpx.HTTPError as e:
                self.log.error("Failed to fetch latest release data from GitHub: %s", e, exc_info=e)
            else:
                if data is not None:
                    version = data["tag_name"]
                    room = self.bot.rooms.get(self.ROOM_ID)
                    if room:
                        old_version, topic = room.topic.split(" | ", 1)
                        old_version = old_version.split(": ", 1)[1].strip()
                        old_version = old_version.split(" ", 1)[0].strip()
                        newer = self.version_is_newer(version, old_version)

                        if version != old_version:
                            self.log.info("Updating topic version from %s to %s", old_version, version)
                            topic = "Current version: %s | %s" % (version, topic)
                            response = await self.bot.update_room_topic(self.ROOM_ID, topic)
                            if not isinstance(response, niobot.RoomPutStateResponse):
                                self.log.warning("Failed to update topic: %s", response)
                            else:
                                if newer:
                                    self.log.info("Updated topic. Notifying room.")
                                    try:
                                        msg_plain = "@room New version of niobot is available! %s (changelog: %s)" % (
                                            version,
                                            data["html_url"]
                                        )
                                        msg_md = await self.bot._markdown_to_html(
                                            "@room New version of niobot is available! [%s](%s) ([changelog](%s))" % (
                                                version,
                                                data["html_url"],
                                                data["html_url"]
                                            )
                                        )
                                        response = await self.bot.room_send(
                                            room.room_id,
                                            "m.room.message",
                                            {
                                                "msgtype": "m.text",
                                                "body": msg_plain,
                                                "format": "org.matrix.custom.html",
                                                "formatted_body": msg_md,
                                                "m.mentions": {
                                                    "room": True
                                                }
                                            }
                                        )
                                        if not isinstance(response, niobot.RoomSendResponse):
                                            raise niobot.MessageException(response=response)
                                    except niobot.MessageException as e:
                                        self.log.error("Failed to notify room: %s", e, exc_info=e)
                                else:
                                    self.log.info(f"Updated topic. Not notifying room ({version} < {old_version}).")
                        else:
                            self.log.info("Version is up to date")
                    else:
                        self.log.warning("Failed to find room %s", self.ROOM_ID)
            self.next_run = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            await asyncio.sleep(1800)  # every half an hour

    @niobot.command("niobot-version")
    async def show_niobot_version(self, ctx: niobot.Context):
        """Shows the different versions of niobot"""
        msg = await ctx.respond("Fetching versions, this may take a few seconds...")

        lines = [
            "Runtime version: %s" % niobot_version.__version__,
        ]
        # Get pypi version
        pypi_response = await self.http_client.get(self.PYPI_API_URL)
        if pypi_response.status_code != 200:
            lines.append(
                "\N{cross mark} Failed to fetch PyPi version (HTTP %d %s)" % (
                    pypi_response.status_code,
                    pypi_response.reason_phrase
                )
            )
        else:
            data = pypi_response.json()
            pypi_version = data["info"]["version"]
            lines.append("PyPi version: [%s](%s)" % (pypi_version, data["info"]["package_url"]))

        # Get GitHub version
        github_response = await self.http_client.get(self.GITHUB_API_URL)
        if github_response.status_code != 200:
            lines.append(
                "\N{cross mark} Failed to fetch GitHub version (HTTP %d %s)" % (
                    github_response.status_code,
                    github_response.reason_phrase
                )
            )
        else:
            data = github_response.json()
            github_version = data["tag_name"]
            lines.append("GitHub version: [%s](%s)" % (github_version, data["html_url"]))

        await msg.edit(
            "\n\n".join(lines)
        )
