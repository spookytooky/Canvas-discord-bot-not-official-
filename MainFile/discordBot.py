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
import storage

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


def _save_courses(courses) -> dict:
    """Persist courses plus today's grade snapshot; return {course_id: (now, before)}.

    Storage is best-effort: if SQLite is unavailable the caller just gets {} and
    /grades still shows the live scores from Canvas.
    """
    if not courses:
        return {}
    try:
        with storage.session() as conn:
            storage.save_courses(conn, courses)
            return storage.grade_changes(conn)
    except Exception:
        logger.exception("Could not save courses to the local database.")
        return {}


def _save_assignments(assignments):
    """Persist assignments and return SQL-built counts, or None if storage failed."""
    if not assignments:
        return None
    try:
        with storage.session() as conn:
            storage.save_assignments(conn, assignments)
            total, courses = storage.deadline_summary(conn)
            by_course = storage.course_deadline_report(conn)
        return {"total": total, "courses": courses, "by_course": by_course}
    except Exception:
        logger.exception("Could not save assignments to the local database.")
        return None


def build_urgent_embed(assignments: list[dict], course_counts=None) -> discord.Embed:
    """The embed /urgent posts: assignments due within 72 hours, or the all-clear.

    ``course_counts`` is an optional [(course_name, count), ...] breakdown read
    back from SQLite, left off whenever the database is unavailable.
    """
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

    if course_counts:
        breakdown = ", ".join(
            f"{pretty_course_name(name)} ×{count}" for name, count in course_counts
        )
        embed.set_footer(text=f"Tracked in SQLite: {breakdown}")

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

        summary = await asyncio.to_thread(_save_assignments, assignments)
        course_counts = summary["by_course"] if summary else None

        await channel.send(embed=build_urgent_embed(assignments, course_counts))
        if assignments:
            logger.info("Daily message sent to channel %s (%d assignment(s) due).", ALERT_CHANNEL_ID, len(assignments))
            if summary:
                logger.info(
                    "Local database reports %d assignment(s) across %d course(s) due in the next 72 hours.",
                    summary["total"],
                    summary["courses"],
                )
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

    try:
        with storage.session() as conn:
            pending, course_count = storage.deadline_summary(conn)
        print(
            f"Local database ready ({storage.DB_PATH.name}): "
            f"{pending} assignment(s) across {course_count} course(s) still due."
        )
    except Exception:
        logger.exception("Local database unavailable; the bot will keep working without it.")

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
        changes = await asyncio.to_thread(_save_courses, courses)
        for info in courses:
            name = pretty_course_name(info.get("name", "Unknown Course"))
            grade = info.get("current_grade", "N/A")
            value = f"Current Grade: {grade}"

            change = changes.get(info.get("id"))
            if change:
                current, previous = change
                if current is not None and previous is not None and abs(current - previous) >= 0.05:
                    arrow = "▲" if current > previous else "▼"
                    value += f"\n{arrow} {abs(current - previous):.2f} since the last recorded day"

            embed.add_field(name=name, value=value, inline=False)

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

    summary = await asyncio.to_thread(_save_assignments, urgent_assignments)
    course_counts = summary["by_course"] if summary else None

    await interaction.followup.send(embed=build_urgent_embed(urgent_assignments, course_counts))


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
