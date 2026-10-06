import asyncio
import datetime
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

import main

load_dotenv()

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

TOKEN = main.require_env(
    "DISCORD_TOKEN",
    "Discord Developer Portal -> your application -> Bot -> Reset Token (README.md, step 3)",
)
SERVER_ID = main.require_env_int(
    "SERVER_ID",
    "Right-click your Discord server -> Copy Server ID (README.md, step 3)",
)

# Optional: the channel that receives the daily message. Blank turns it off.
_alert_channel = (os.getenv("ALERT_CHANNEL_ID") or "").strip()
ALERT_CHANNEL_ID = None
if _alert_channel:
    if not _alert_channel.isdigit():
        print("ALERT_CHANNEL_ID must be a numeric Discord channel ID, or blank.")
        print("Right-click a channel -> Copy Channel ID (see README.md, settings reference).")
        sys.exit(1)
    ALERT_CHANNEL_ID = int(_alert_channel)

guild_id = discord.Object(id=SERVER_ID)

canvas = main.CanvasClient()

handler = logging.FileHandler(filename=Path(__file__).with_name("discord.log"), encoding="utf-8", mode="w")
intents = discord.Intents.default()

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


def pretty_course_name(name: str) -> str:
    """Turn '202680.COP3530.83047:Data Structures' into 'COP3530 - Data Structures'."""
    cleaned = (name or "Unknown Course").replace(":", ".")
    parts = cleaned.split(".")
    if len(parts) >= 4 and parts[1].strip() and parts[3].strip():
        return f"{parts[1].strip()} - {parts[3].strip()}"
    return name or "Unknown Course"


def canvas_error_embed(error: main.CanvasError) -> discord.Embed:
    description = (
        f"{error}\n\n"
        "Check CANVAS_TOKEN and CANVAS_BASE_URL in your .env file "
        "(see README.md -> Troubleshooting)."
    )
    return discord.Embed(title="Couldn't reach Canvas", description=description, color=discord.Color.red())


def build_urgent_embed(assignments: list[dict]) -> discord.Embed:
    """The embed /urgent posts: assignments due within 72 hours, or the all-clear."""
    if not assignments:
        return discord.Embed(
            title="🎉 All Caught Up!",
            description="You have no assignments due in the next 72 hours.",
            color=discord.Color.green(),
        )

    if assignments[0]["hours_left"] <= 24.0:
        embed_color = discord.Color.red()
    else:
        embed_color = discord.Color.gold()

    embed = discord.Embed(
        title="📚 Assignments due in 24-72 hours",
        description=f"You have {len(assignments)} pending assignment(s):",
        color=embed_color,
    )

    for info in assignments:
        course_name = info.get("course", "").split(":")[-1]
        name = info["name"]
        url = info.get("url")
        hours_left = info["hours_left"]

        title_link = f"[{name}]({url})" if url else name

        embed.add_field(
            name=f"📌 {course_name}",
            value=f"{title_link}\n⏳ Due in: {hours_left}h",
            inline=False,
        )

    return embed


# Change the hour/minute below to move the daily message (24-hour clock, Eastern time).
target_time = datetime.time(hour=12, minute=0, tzinfo=EASTERN)


def next_daily_run() -> datetime.datetime:
    """The next time the daily message is due, in Eastern time."""
    now = datetime.datetime.now(EASTERN)
    upcoming = datetime.datetime.combine(now.date(), target_time)
    if upcoming <= now:
        upcoming += datetime.timedelta(days=1)
    return upcoming


@tasks.loop(time=target_time)
async def send_daily_message():
    try:
        channel = bot.get_channel(ALERT_CHANNEL_ID)
        if channel is None:
            logger.warning(
                "Daily message skipped: couldn't find channel %s (is the bot in that channel?).",
                ALERT_CHANNEL_ID,
            )
            return

        try:
            assignments = await asyncio.to_thread(canvas.get_urgent_assignments)
        except main.CanvasError as error:
            logger.exception("Daily message: Canvas request failed")
            await channel.send(embed=canvas_error_embed(error))
            return

        await channel.send(embed=build_urgent_embed(assignments))
        if assignments:
            logger.info("Daily message sent to channel %s (%d assignment(s) due).", ALERT_CHANNEL_ID, len(assignments))
        else:
            logger.info("Daily message sent to channel %s (nothing due in the next 72 hours).", ALERT_CHANNEL_ID)
    except discord.HTTPException as error:
        logger.exception("Daily message failed to send: %s", error)
    except Exception:
        logger.exception("Daily message failed unexpectedly; the loop will keep running.")


@bot.event
async def on_ready():
    await bot.tree.sync(guild=guild_id)
    print(f"Logged in as {bot.user.name} and synced tree to guild {SERVER_ID}")

    if ALERT_CHANNEL_ID is None:
        print("Daily message is off (ALERT_CHANNEL_ID is blank in .env).")
        return

    if not send_daily_message.is_running():
        send_daily_message.start()
        send_time = target_time.strftime("%I:%M %p").lstrip("0")
        schedule_line = f"Daily message scheduled for {send_time} Eastern (next: {next_daily_run():%Y-%m-%d %I:%M %p %Z})"
        print(schedule_line)
        logger.info(schedule_line)


# /grades shows the grades for each class
@bot.tree.command(name="grades", description="Check grades for each class", guild=guild_id)
async def grades(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        courses = await asyncio.to_thread(canvas.get_active_courses)
    except main.CanvasError as error:
        await interaction.followup.send(embed=canvas_error_embed(error))
        return

    embed = discord.Embed(
        title="📚 Grade Overview",
        description="Current posted scores across your active courses:",
        color=discord.Color.green(),
    )

    if not courses:
        embed.description = "No active courses found for the current term."
    else:
        for info in courses:
            name = pretty_course_name(info.get("name", "Unknown Course"))
            grade = info.get("current_grade", "N/A")
            embed.add_field(name=name, value=f"Current Grade: {grade}", inline=False)

    await interaction.followup.send(embed=embed)


# /urgent sends all assignments due in 72 hours
@bot.tree.command(name="urgent", description="Sends list of assignments due in 24-72 hours", guild=guild_id)
async def urgent(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        urgent_assignments = await asyncio.to_thread(canvas.get_urgent_assignments)
    except main.CanvasError as error:
        await interaction.followup.send(embed=canvas_error_embed(error))
        return

    await interaction.followup.send(embed=build_urgent_embed(urgent_assignments))


# /links sends links for all classes
@bot.tree.command(name="links", description="Get the links for your classes", guild=guild_id)
async def links(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        courses = await asyncio.to_thread(canvas.get_active_courses)
    except main.CanvasError as error:
        await interaction.followup.send(embed=canvas_error_embed(error))
        return

    embed = discord.Embed(
        title="Course Links",
        description="Links for all of your active courses",
        color=discord.Color.blue(),
    )

    if not courses:
        embed.description = "No active courses found for the current term."
    else:
        for info in courses:
            name = pretty_course_name(info.get("name", "Unknown Course"))
            class_id = info.get("id")
            link = f"{canvas.web_url}/courses/{class_id}/modules"
            embed.add_field(name=name, value=link, inline=False)

    await interaction.followup.send(embed=embed)

if __name__ == "__main__":
    # root_logger=True routes this file's log records (plus discord's) into discord.log.
    bot.run(TOKEN, log_handler=handler, log_level=logging.INFO, root_logger=True)
