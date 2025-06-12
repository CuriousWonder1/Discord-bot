import discord
from discord.ext import commands
import os
import re
import json
from datetime import datetime, timedelta, timezone
import asyncio
from flask import Flask
from threading import Thread
import base64
import requests

GUILD_ID = 457619956687831050
EVENTS_FILE = "events.json"
STAFF_ROLE_IDS = {578725917258416129, 879592909203197952}

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

from discord import app_commands

app = Flask(__name__)

@app.route('/')
def home():
    print("\U0001F501 Ping received from UptimeRobot (or browser)")
    return "Bot is online!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    try:
        synced = await bot.tree.sync(guild=guild)
        print(f"\u2705 Synced {len(synced)} slash command(s) to guild {GUILD_ID}")
    except Exception as e:
        print(f"\u274C Sync failed: {e}")

    await schedule_upcoming_events()

def staff_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id in STAFF_ROLE_IDS for role in interaction.user.roles)
    return app_commands.check(predicate)

def fetch_github_events():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("\u274C GITHUB_TOKEN not set!")
        return []

    url = "https://api.github.com/repos/CuriousWonder1/Discord-bot/contents/events.json"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        content = response.json()["content"]
        return json.loads(base64.b64decode(content).decode())
    else:
        print(f"\u274C Failed to fetch events.json: {response.status_code}")
        print("Response:", response.text)
        return []

def commit_github_events(data):
    token = os.getenv("GITHUB_TOKEN")
    branch = "main"
    if not token:
        print("\u274C GITHUB_TOKEN not set!")
        return

    url = "https://api.github.com/repos/CuriousWonder1/Discord-bot/contents/events.json"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    get_resp = requests.get(url, headers=headers)
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
    else:
        print(f"\u26A0\uFE0F Couldn't retrieve current file SHA: {get_resp.status_code}")
        print("Response:", get_resp.text)
        sha = None

    content = base64.b64encode(json.dumps([
        {**e, "start_time": e["start_time"].isoformat()} for e in data
    ], indent=4).encode()).decode()

    payload = {
        "message": "Update events",
        "content": content,
        "branch": branch
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(url, headers=headers, json=payload)
    if put_resp.status_code in (200, 201):
        print("\u2705 events.json updated on GitHub.")
    else:
        print("\u274C Failed to update events.json on GitHub:")
        print("Status:", put_resp.status_code)
        print("Response:", put_resp.text)

def load_events():
    data = fetch_github_events()
    for e in data:
        if isinstance(e["start_time"], str):
            e["start_time"] = datetime.fromisoformat(e["start_time"])
    return data

def save_events():
    commit_github_events(events)

events = load_events()

def parse_time_delay(time_str: str) -> int:
    match = re.fullmatch(r"(\d+)([smhd])", time_str.lower())
    if not match:
        raise ValueError("Invalid time format. Use number + s/m/h/d, e.g. 30s, 5m, 48h, 2d.")
    value, unit = match.groups()
    value = int(value)
    return value * {"s":1, "m":60, "h":3600, "d":86400}[unit]

async def announce_event(event):
    now = datetime.now(tz=timezone.utc)
    delay = (event["start_time"] - now).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print(f"Failed to get guild {GUILD_ID} for event {event['name']}")
        return

    channel = next((ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages), None)
    if channel is None:
        print(f"No suitable channel found for event {event['name']}")
        return

    role_mention = "<@&828406807285202974>"
    await channel.send(role_mention, allowed_mentions=discord.AllowedMentions(roles=True))

    embed = discord.Embed(
        title=event["name"].upper(),
        description=event["info"],
        color=discord.Color.blue()
    )

    if event.get("reward1"):
        embed.add_field(name="\U0001F381 1st Place Reward", value=event["reward1"], inline=False)
    if event.get("reward2"):
        embed.add_field(name="\U0001F381 2nd Place Reward", value=event["reward2"], inline=False)
    if event.get("reward3"):
        embed.add_field(name="\U0001F381 3rd Place Reward", value=event["reward3"], inline=False)

    embed.add_field(
        name="",
        value="To participate in this event, tick the reaction below and you will be given the Participant role.",
        inline=False
    )

    embed.set_footer(text=f"Created by {event['creator']['name']}")

    message = await channel.send(embed=embed)
    await message.add_reaction("\u2705")

    event["started"] = True
    save_events()
    print(f"Event announced: {event['name']}")

async def schedule_upcoming_events():
    now = datetime.now(tz=timezone.utc)
    for event in events:
        if isinstance(event["start_time"], str):
            event["start_time"] = datetime.fromisoformat(event["start_time"])
        if not event.get("started", False) and event["start_time"] > now:
            bot.loop.create_task(announce_event(event))


@bot.tree.command(name="createevent", description="Create an event", guild=discord.Object(id=GUILD_ID))
@staff_only()
async def createevent(interaction: discord.Interaction, name: str, info: str, delay: str = "0s", reward1: str = "", reward2: str = "", reward3: str = ""):
    try:
        delay_seconds = parse_time_delay(delay)
    except ValueError as ve:
        await interaction.response.send_message(
            "❌ Invalid time format. Use number + s/m/h/d, e.g. 30s, 5m, 48h, 2d.",
            ephemeral=True
        )
        return

    start_time = datetime.now(tz=timezone.utc) + timedelta(seconds=delay_seconds)
    creator = {
        "id": interaction.user.id,
        "name": str(interaction.user)
    }

    event_data = {
        "name": name,
        "info": info,
        "reward1": reward1,
        "reward2": reward2,
        "reward3": reward3,
        "start_time": start_time,
        "started": False,
        "creator": creator
    }

    events.append(event_data)
    save_events()

    if delay_seconds > 0:
        await interaction.response.send_message(f"⏳ Event '{name}' will be posted in {delay_seconds} seconds.", ephemeral=True)
        bot.loop.create_task(announce_event(event_data))
    else:
        await interaction.response.defer(ephemeral=True)
        bot.loop.create_task(announce_event(event_data))
        await interaction.followup.send(f"✅ Event '{name}' has been posted!", ephemeral=True)


@bot.tree.command(name="end", description="Sends the event info and clears the Participant role", guild=discord.Object(id=GUILD_ID))
@staff_only()
async def end(interaction: discord.Interaction):
    now = datetime.now(tz=timezone.utc)
    current_events = load_events()

    await interaction.response.send_message("Ending event and removing Participant role.", ephemeral=True)

    # Remove "Participant" role from everyone who has it
    guild = interaction.guild
    participant_role = discord.utils.get(guild.roles, name="Participant")
    if participant_role:
        for member in guild.members:
            if participant_role in member.roles:
                try:
                    await member.remove_roles(participant_role, reason="Event ended")
                    print(f"Removed Participant role from {member.display_name}")
                except Exception as e:
                    print(f"Failed to remove role from {member.display_name}: {e}")
    else:
        print("Participant role not found.")

    # Prepare and send the embed
    for e in current_events:
        if isinstance(e["start_time"], str):
            e["start_time"] = datetime.fromisoformat(e["start_time"])

    upcoming = [e for e in current_events if e["start_time"] > now and not e["started"]]

    description_text = (
        "This channel is temporarily closed until an event is being held. It will reopen once the event starts.\n"
        "If you have any questions about upcoming events, feel free to ping the host, DM them, or ask in ⁠https://discord.com/channels/457619956687831050/666452996967628821\n\n"
    )

    if upcoming:
        description_text += "🗓️ **Current Upcoming Events:**"
    else:
        description_text += "🚫 **There are currently no upcoming events scheduled.**"

    embed = discord.Embed(
        title="🎉 Event Information",
        description=description_text,
        color=discord.Color.orange()
    )

    for e in upcoming:
        embed.add_field(
            name=e["name"],
            value=f"Starts <t:{int(e['start_time'].timestamp())}:F>\nCreated by: <@{e['creator']['id']}>\n",
            inline=False
        )

    embed.add_field(
        name="",
        value=f"Keep an eye out for future events in here or ⁠https://discord.com/channels/457619956687831050/1349087527557922988! 👀",
        inline=False
        )

    try:
        await interaction.channel.send(embed=embed)
    except discord.InteractionResponded:
        pass


@bot.tree.command(name="events", description="Shows all upcoming events", guild=discord.Object(id=GUILD_ID))
async def events_command(interaction: discord.Interaction):
    now = datetime.now(tz=timezone.utc)
    current_events = load_events()
    for e in current_events:
        if isinstance(e["start_time"], str):
            e["start_time"] = datetime.fromisoformat(e["start_time"])

    upcoming = [e for e in current_events if e["start_time"] > now and not e["started"]]

    if not upcoming:
        await interaction.response.send_message("There are no upcoming events planned.")
        return

    embed = discord.Embed(title="📅 Upcoming Events", color=discord.Color.green())
    for e in upcoming:
        embed.add_field(
            name=e["name"],
            value=f"Starts <t:{int(e['start_time'].timestamp())}:F>\nCreated by: <@{e['creator']['id']}>",
            inline=False
        )
    await interaction.response.send_message(embed=embed)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.emoji.name != "✅" or payload.user_id == bot.user.id:
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = discord.utils.get(guild.roles, name="Participant") if guild else None
    if member and role and role not in member.roles:
        await member.add_roles(role)
        print(f"✅ Assigned Participant role to {member.display_name}")

@bot.event
async def on_raw_reaction_remove(payload):
    if payload.emoji.name != "✅":
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    role = discord.utils.get(guild.roles, name="Participant") if guild else None
    if member and role and role in member.roles:
        await member.remove_roles(role)
        print(f"❎ Removed Participant role from {member.display_name}")


keep_alive()
print("🔁 Starting bot...")
bot.run(os.getenv("DISCORD_TOKEN"))

port = int(os.environ.get("PORT", 8080))  # Use Render's assigned port or default to 8080
app.run(host='0.0.0.0', port=port)
