import niobot
import io


class ManagementModule(niobot.Module):
    @niobot.command(name="rooms.list")
    @niobot.is_owner()
    async def list_rooms(self, ctx: niobot.Context, largest_first: bool = False):
        """Lists all rooms the bot is in"""
        if not self.bot.is_owner(ctx.message.sender):
            return await ctx.respond("You are not my owner!")

        lines = ["```\n"]
        if largest_first:
            rooms = sorted(self.bot.rooms.values(), key=lambda x: x.member_count, reverse=True)
        else:
            rooms = iter(self.bot.rooms.values())
        for room in rooms:
            lines.append("%s (%s) - %d members\n" % (room.room_id, room.display_name, room.member_count))
        lines.append("```")
        await ctx.respond("".join(lines))

    @niobot.command(name="rooms.leave")
    @niobot.is_owner()
    async def leave(self, ctx: niobot.Context, room: str = None):
        """Leaves a room

        [room] can either be a room ID or --empty, which will leave all rooms with no members."""
        if not self.bot.is_owner(ctx.message.sender):
            return await ctx.respond("You are not my owner!")
        if room is None:
            room = ctx.room.room_id

        if room == "--empty":
            msg = await ctx.respond("Gathering rooms...")
            log = io.BytesIO()

            def write_log(m: str) -> None:
                log.write(m.encode() + b"\n")

            targets = []
            for room in self.bot.rooms.values():
                members = room.users.copy()
                if self.bot.user_id not in members:
                    write_log(
                        'Room %s (%s) is in the room list, but I am not a member?' % (
                            room.room_id,
                            room.display_name,
                        )
                  )
                    continue
                members.pop(self.bot.user_id, None)
                write_log(
                    'Room %s (%s) had %d members after popping myself.' % (
                        room.room_id,
                        room.display_name,
                        len(members),
                    )
                )
                if len(members) == 0:
                    write_log("- Added %s (%s) to the target list." % (room.room_id, room.display_name))
                    targets.append(room.room_id)
            log.seek(0)
            await msg.edit('Leaving %d rooms...' % len(targets))
            for room in targets:
                await self.bot.room_leave(room)
                await self.bot.room_forget(room)

            value = log.getvalue()
            if len(value) > 1000:
                log.seek(0)
                await msg.delete()
                await ctx.respond(file=await niobot.FileAttachment.from_file(log, "leave.log"))
            else:
                await msg.edit('Done! Log:\n```%s```' % log.read().decode("utf-8"))
        else:
            msg = await ctx.respond("Leaving room %s" % room)
            response = await self.bot.room_leave(room)
            if isinstance(response, niobot.RoomLeaveError):
                await msg.edit("Failed to leave room %s: %s" % (room, response.message))
            else:
                await msg.edit("Left room %s" % room)

    @niobot.command(name="rooms.members")
    @niobot.is_owner()
    async def members_cmd(self, ctx: niobot.Context, room_id: str = None, cached: int = 1):
        """Lists members of a given room"""
        if room_id is None:
            room_id = ctx.room.room_id

        room = self.bot.rooms.get(room_id)
        if room is None:
            return await ctx.respond("I am not in room %s." % room_id)

        if cached:
            members = room.users.copy()
        else:
            members = (await self.bot.joined_members(room_id)).members
            members = {x.user_id: x for x in members}

        if len(members) == 0:
            return await ctx.respond("Room %s has no members." % room_id)
        elif len(members) == 1:
            if list(members.keys())[0] == self.bot.user_id:
                return await ctx.respond("I am the only member of room %s." % room_id)

        if not self.bot.is_owner(ctx.message.sender):
            if ctx.message.sender not in members:
                return await ctx.respond("You do not have permission to view %r's members." % room_id)

        if len(members) > 10:
            x = io.BytesIO()
            x.write('{:,} members:\n'.format(len(members)).encode("utf-8"))
            for n, item in enumerate(members.values(), 1):
                x.write(('{0:,}. {1.display_name} ({1.user_id})\n'.format(n, item)).encode('utf-8'))
            x.seek(0)
            return await ctx.respond(file=await niobot.FileAttachment.from_file(x, "members.txt"))
        else:
            return await ctx.respond(
                '```\n%s\n```' % '\n'.join(
                    '{0:,}. {1.display_name} ({1.user_id})'.format(n, item)
                    for n, item in enumerate(members.values(), 1)
                )
            )

    @niobot.command(name="rooms.join")
    @niobot.is_owner()
    async def join(self, ctx: niobot.Context, room: str = None):
        """Joins a room via its ID.

        It will be easier to simply invite the bot into the designated room."""
        if room is None:
            room = ctx.room.room_id
        msg = await ctx.respond("Joining room %s" % room)
        response = await self.bot.join(room)
        if isinstance(response, niobot.JoinError):
            await msg.edit("Failed to join room %s: %s" % (room, response.message))
        else:
            await msg.edit("Joined room %s" % room)
