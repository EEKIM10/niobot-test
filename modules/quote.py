import asyncio
import io
import re
import time

import niobot
import httpx
import tempfile


class QuoteModule(niobot.Module):
    def __init__(self, bot):
        super().__init__(bot)
        self.lock = asyncio.Lock()

    @niobot.command("quote", aliases=['q'])
    async def quote(self, ctx: niobot.Context, verbose: bool = False):
        """Generate a random quote.
        
        The source is https://inspirobot.me/"""
        msg = await ctx.respond("Waiting...")
        async with self.lock:
            with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
                async with httpx.AsyncClient() as client:
                    start = time.time()
                    response = await client.get("https://inspirobot.me/api?generate=true")
                    end = time.time()
                    gen_time = (end - start)
                    if response.status_code != 200:
                        await msg.edit("Something happened!")
                        return
                    url = response.text
                    start = time.time()
                    response = await client.get(url)
                    end = time.time()
                    dl_time = (end - start)
                    if response.status_code != 200:
                        await msg.edit("Something happened and nearly succeeded!")
                        return
                    with open(tmp.name, "wb") as f:
                        f.write(response.content)
                    attachment = await niobot.ImageAttachment.from_file(tmp.name)
                    start = time.time()
                    await ctx.respond(url, file=attachment)
                    end = time.time()
                    ul_time = (end - start)
                    if verbose:
                        await msg.edit(
                            "Response times:\n* Generate: {:,.2f}ms\n* Download: {:,.2f}ms\n* Upload: {:,.2f}ms".format(
                                gen_time * 1000,
                                dl_time * 1000,
                                ul_time * 1000
                            )
                        )
                    else:
                        await msg.delete("command completed")

    @niobot.command(name="xkcd")
    async def xkcd_command(self, ctx: niobot.Context, comic_number: int = None):
        """Fetches an XKCD comic.

        If none is provided, a random one is chosen."""
        session = httpx.AsyncClient()
        if comic_number is None:
            response = await session.get("https://c.xkcd.com/random/comic/", follow_redirects=False)
            if response.status_code != 302:
                await ctx.respond("Unable to fetch a random comic (HTTP %d)" % response.status_code)
                return
            comic_number = re.match(r"https://xkcd.com/(\d+)/", response.headers["Location"]).group(1)

        response = await session.get("https://xkcd.com/%d/info.0.json" % comic_number)
        if response.status_code != 200:
            await ctx.respond("Unable to fetch comic %d (HTTP %d)" % (comic_number, response.status_code))
            return

        data = response.json()
        download = await session.get(data["img"])
        if download.status_code != 200:
            await ctx.respond("Unable to download comic %d (HTTP %d)" % (comic_number, download.status_code))
            return

        with tempfile.NamedTemporaryFile("wb", prefix="xkcd-comic-", suffix=".png") as file:
            file.write(download.content)
            file.flush()
            file.seek(0)
            fn = re.sub(r'[^\w\d-]', '_', data['safe_title']) + ".png"
            attachment = await niobot.ImageAttachment.from_file(file.name)
            await ctx.respond(data["alt"], file=attachment)
