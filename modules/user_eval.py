"""
Module that provides code-evaluation commands.
This entire module is locked to NioBot.owner.
"""
import textwrap
import niobot
import asyncio
import re
import os
import time
import contextlib
import pprint
import sys
import secrets
import traceback
import io
import functools


class EvalModule(niobot.Module):
    def __init__(self, bot: niobot.NioBot):
        if not bot.owner_id:
            raise RuntimeError("No owner ID set in niobot. Refusing to load for security reasons.")
        super().__init__(bot)
    
    async def owner_check(self, ctx: niobot.Context) -> bool:
        """Checks if the current user is the owner, and if not, gives them a very informative message."""
        if not self.bot.is_owner(ctx.message.sender):
            await ctx.respond(
                "\N{cross mark} Only the owner of this bot can run evaluation commands. Nice try, though!"
            )
            return False
        return True

    @staticmethod
    def undress_codeblock(code: str) -> str:
        """Removes any code block syntax from the given string."""
        code = code.strip()
        lines = code.splitlines()
        if len(lines) > 2:
            if lines[0].startswith("```") and code.endswith("```"):
                return "\n".join(lines[1:-1])
            if lines[0].startswith("```"):
                re.sub(r"```(.{0,10})\n", "", code, 1)
            if lines[-1].endswith("```"):
                re.sub(r"\n```$", "", code, 1)
        if code.startswith("`") and code.endswith("`"):
            return code[1:-1]
        return code

    @niobot.command("eval")
    async def python_eval(self, ctx: niobot.Context, code: str):
        """Evaluates python code.
        
        All code is automatically wrapped in an async function, so you can do top-level awaits.
        You must return a value for it to be printed, or manually print() it.

        The following special variables are available:

        * `ctx` - The current context.
        * `loop` - The current event loop.
        * `stdout` - A StringIO object that you can write to print to stdout.
        * `stderr` - A StringIO object that you can write to print to stderr.
        * `_print` - The builtin print function.
        * `print` - A partial of the builtin print function that prints to the stdout string IO
        """
        if not await self.owner_check(ctx):
            return
        code = self.undress_codeblock(code)
        stdout = io.StringIO()
        stderr = io.StringIO()

        g = {
            **globals().copy(),
            **locals().copy(),
            "ctx": ctx,
            "loop": asyncio.get_event_loop(),
            "stdout": stdout,
            "stderr": stderr,
            "_print": print,
            "print": functools.partial(print, file=stdout)
        }

        code = textwrap.indent(code, "    ")
        code = f"async def __eval():\n{code}"
        msg = await ctx.respond("Evaluating...")
        e = await self.client.add_reaction(ctx.room, ctx.message, "\N{hammer}")
        # noinspection PyBroadException
        try:
            start = time.time() * 1000
            runner = await niobot.run_blocking(exec, code, g)
            end_compile = time.time() * 1000
            result = await g["__eval"]()
            end_exec = time.time() * 1000
            total_time = end_exec - start
            time_str = "%.2fms (compile: %.2fms, exec: %.2fms)" % (
                total_time, end_compile - start, end_exec - end_compile
            )
            lines = ["Time: " + time_str + "\n"]
            if result is not None:
                if isinstance(result, (list, dict, tuple, set, int, float)):
                    result = pprint.pformat(
                        result,
                        indent=4,
                        width=80,
                        underscore_numbers=True,
                    )
                lines += ["Result:\n", "```", result, "```\n"]
            if stdout.getvalue():
                lines.append("Stdout:\n```\n" + stdout.getvalue() + "```")
            if stderr.getvalue():
                lines.append("Stderr:\n```\n" + stderr.getvalue() + "```")
            await ctx.client.add_reaction(ctx.room, ctx.message, "\N{white heavy check mark}")
            await msg.edit("\n".join(lines))
        except Exception as e:
            await ctx.client.add_reaction(ctx.room, ctx.message, "\N{cross mark}")
            await msg.edit(f"Error:\n```py\n{traceback.format_exc()}```")
        finally:
            await ctx.client.redact_reaction(ctx.room, e)
