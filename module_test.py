import os

import niobot


class MyModule(niobot.Module):
    def __init__(self, *args):
        super().__init__(*args)
        self.id = os.urandom(32)

    @niobot.command(name="module-test")
    async def module_test(self, ctx: niobot.Context):
        """Tests module"""
        await ctx.reply(f"'MyModule' is working! {self.id == ctx.command.module.id}")

