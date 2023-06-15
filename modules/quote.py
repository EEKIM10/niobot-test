import time

import niobot
import httpx
import tempfile


class QuoteModule(niobot.Module):
    @niobot.command("quote", aliases=['q'])
    async def quote(self, ctx: niobot.Context):
        """Generate a random quote.
        
        The source is https://inspirobot.me/"""
        with tempfile.NamedTemporaryFile(suffix=".jpg") as tmp:
            async with httpx.AsyncClient() as client:
                start = time.time()
                response = await client.get("https://inspirobot.me/api?generate=true")
                end = time.time()
                gen_time = (end - start)
                if response.status_code != 200:
                    await ctx.respond("Something happened!")
                    return
                url = response.text
                start = time.time()
                response = await client.get(url)
                end = time.time()
                dl_time = (end - start)
                if response.status_code != 200:
                    await ctx.respond("Something happened and nearly succeeded!")
                    return
                with open(tmp.name, "wb") as f:
                    f.write(response.content)
                attachment = await niobot.MediaAttachment.from_file(tmp.name)
                start = time.time()
                await ctx.respond(url, file=attachment)
                end = time.time()
                ul_time = (end - start)
                if '--verbose' in ctx.args:
                    await ctx.respond(
                        "Response times:\n* Generate: {:,.2f}ms\n* Download: {:,.2f}ms\n* Upload: {:,.2f}ms".format(
                            gen_time * 1000,
                            dl_time * 1000,
                            ul_time * 1000
                        )
                    )
