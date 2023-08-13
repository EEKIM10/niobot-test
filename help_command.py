from types import NoneType
import niobot
from niobot.utils.help_command import *


async def custom_help(ctx: niobot.Context, command_name: str = None):
    """Displays help about a command."""
    if command_name is not None:
        # check if the first arg is a module name
        name = command_name
        for module in ctx.bot.modules:
            mn = type(module).__name__
            if mn.endswith("Module"):
                mn = mn[:-6]
            matches = any(
                (
                    mn.lower() == name.lower() and ctx.bot.get_command(name) is None,
                    mn == name,  # exact match, case sensitive
                )
            )
            if matches:
                # list all commands in this module
                cmds = []
                for cmd in ctx.bot.commands:
                    command = ctx.bot.get_command(cmd)
                    if command.module == module:
                        if command not in cmds:
                            cmds.append(command)
                if cmds:
                    lines = ['### Module {}'.format(mn)]
                    for cmd in cmds:
                        lines.append(
                            "- {}: {}".format(
                                format_command_line(ctx.client.command_prefix, cmd),
                                get_short_description(cmd)
                            )
                        )
                    lines.append("")
                    return await ctx.respond("\n".join(lines))
                else:
                    return await ctx.respond("No commands found in module {}".format(mn))
        # Defer showing help for a particular command to the built-in help command.
        return await help_command_callback(ctx)
    else:
        mods = {}
        seen = []
        for cmd in ctx.bot.commands:
            cmd = ctx.bot.get_command(cmd)
            if cmd in seen:
                continue
            seen.append(cmd)
            if cmd.module not in mods:
                mods[cmd.module] = []
            mods[cmd.module].append(cmd)
        
        
        lines = []
        for mod, cmds in mods.items():
            if mod:
                mod = type(mod).__name__
            else:
                mod = "N/A"
            
            if mod.endswith("Module"):
                mod = mod[:-6]

            lines.append(f"### Module {mod!r}")
            for cmd in cmds:
                lines.append(
                    "- {}: {}".format(
                        format_command_line(ctx.client.command_prefix, cmd),
                        get_short_description(cmd)
                    )
                )
            lines.append("")
        
        await ctx.respond("\n".join(lines))
