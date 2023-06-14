import niobot
import httpx
import tempfile


class QuoteModule(niobot.Module):
    @niobot.command("quote", aliases=['q'])
    async def quote(self, ctx: niobot.Context):
        """Generate a random quote.
        
        The source is https://inspirobot.me/"""
        with tempfile.TemporaryFile(suffix=".jpg") as tmp:
            async with httpx.AsyncClient() as client:
                response = await client.get("https://inspirobot.me/api?generate=true")
                if response.status_code != 200:
                    await ctx.reply("Something happened!")
                    return
                url = response.text
                response = await client.get(url)
                if response.status_code != 200:
                    await ctx.reply("Something happened and nearly succeeded!")
                    return
                with open(tmp, "wb") as f:
                    f.write(response.content)
                attachment = await niobot.MediaAttachment.from_file(tmp)
                await ctx.reply(url, file=attachment)
