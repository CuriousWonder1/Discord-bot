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

GUILD_ID = 1372905565742960771
EVENTS_FILE = "events.json"
STAFF_ROLE_IDS = {1377216937989505074, 1377217110551429140}

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
    print("🔁 Ping received from UptimeRobot (or browser)")
    return "Bot is online!"

def run():
    app.run(host='0.0.0.0', port=8080)  # Replit expects port 3000, but 8080 is fine if configured

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

@bot.event
async def on_ready():
    guild = discord.Object(id=GUILD_ID)
    
    try:
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} slash command(s) to guild {GUILD_ID}")
    except Exception as e:
        print(f"❌ Sync failed: {e}")
    
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
        print("❌ GITHUB_TOKEN not set!")
        return []

    url = "https://api.github.com/repos/CuriousWonder1/Discord-bot/contents/events.json"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        try:
            content = response.json()["content"]
            return json.loads(base64.b64decode(content).decode())
        except Exception as e:
            print("❌ Failed to decode events.json content:", e)
            print("Response:", response.text)
            return []
    else:
        print(f"❌ Failed to fetch events.json: {response.status_code}")
        print("Response:", response.text)
        return []

def commit_github_events(data):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("❌ GITHUB_TOKEN not set!")
        return

    url = "https://api.github.com/repos/CuriousWonder1/Discord-bot/contents/events.json"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    # Get current file SHA for update
    get_resp = requests.get(url, headers=headers)
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")
    else:
        print(f"⚠️ Couldn't retrieve current file SHA: {get_resp.status_code}")
        print("Response:", get_resp.text)
        sha = None

    # Prepare content (convert datetime to ISO string)
    serializable_data = [
        {**e, "start_time": e["start_time"].isoformat() if isinstance(e["start_time"], datetime) else e["start_time"]}
        for e in data
    ]
    content = base64.b64encode(json.dumps(serializable_data, indent=4).encode()).decode()

    payload = {
        "message": "Update events",
        "content": content,
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(url, headers=headers, json=payload)
    if put_resp.status_code in (200, 201):
        print("✅ events.json updated on GitHub.")
    else:
        print("❌ Failed to update events.json on GitHub:")
        print("Status:", put_resp.status_code)
        print("Response:", put_resp.text)

def load_events():
    data = fetch_github_events()
    for e in data:
        if isinstance(e.get("start_time"), str):
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

    # Find first channel where bot can send messages
    channel = None
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            channel = ch
            break
    if channel is None:
        print(f"No suitable channel found for event {event['name']}")
        return

    role_mention = "<@&1377228208302329936>"
    await channel.send(role_mention, allowed_mentions=discord.AllowedMentions(roles=True))

    embed = discord.Embed(
        title=event["name"].upper(),
        description=event["info"],
        color=discord.Color.blue()
    )

    if event.get("reward1"):
        embed.add_field(name="🎁 1st Place Reward", value=event["reward1"], inline=False)
    if event.get("reward2"):
        embed.add_field(name="🎁 2nd Place Reward", value=event["reward2"], inline=False)
    if event.get("reward3"):
        embed.add_field(name="🎁 3rd Place Reward", value=event["reward3"], inline=False)

    embed.add_field(
        name="",
        value="To participate in this event, tick the reaction below and you will be given the Participant role.",
        inline=False
    )

    embed.set_footer(text=f"Created by {event['creator']['name']}")

    message = await channel.send(embed=embed)
    await message.add_reaction("✅")

    event["started"] = True
    save_events()
    print(f"Event announced: {event['name']}")

async def schedule_upcoming_events():
    now = datetime.now(tz=timezone.utc)
    for event in events:
        if isinstance(event.get("start_time"), str):
            event["start_time"] = datetime.fromisoformat(event["start_time"])
        if not event.get("started", False) and event["start_time"] > now:
            bot.loop.create_task(announce_event(event))

@bot.tree.command(name="createevent", description="Create an event", guild=discord.Object(id=GUILD_ID))
@staff_only()
async def createevent(interaction: discord.Interaction, name: str, info: str, delay: str = "0s", reward1: str = "", reward2: str = "", reward3: str = ""):
    try:
        delay_seconds = parse_time_delay(delay)
    except ValueError:
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

    for e in current_events:
        if isinstance(e.get("start_time"), str):
            e["start_time"] = datetime.fromisoformat(e["start_time"])

    upcoming = [e for e in current_events if e["start_time"] > now and not e.get("started", False)]

    description_text = (
        "This channel is temporarily closed until an event is being held. It will reopen once the event starts.\n"
        "If you have any questions about upcoming events, feel free to ping the host, DM them, or ask in ⁠https://discord.com/channels/457619956687831050/666452996967628821\n"
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
            value=f"Starts <t:{int(e['start_time'].timestamp())}:F>\nCreated by: <@{e['creator']['id']}>\nKeep an eye out for future events in here or https://discord.com/channels/457619956687831050/1349087527557922988! 👀",
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
    upcoming = [e for e in current_events if e["start_time"] > now and not e.get("started", False)]

    if not upcoming:
        await interaction.response.send_message("🚫 There are currently no upcoming events.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎉 Upcoming Events",
        description="Here are the scheduled events:",
        color=discord.Color.green()
    )

    for e in upcoming:
        embed.add_field(
            name=e["name"],
            value=f"Starts <t:{int(e['start_time'].timestamp())}:F>\nCreated by: <@{e['creator']['id']}>",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    member = guild.get_member(payload.user_id)
    if member is None:
        return

    channel = guild.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    # Check if reaction is ✅ and message is one of our event announcements
    if str(payload.emoji) != "✅":
        return

    # Identify the event this message belongs to
    # Check if message embed matches event info
    for event in events:
        if event.get("started") and message.embeds:
            embed = message.embeds[0]
            if embed.title and embed.title.lower() == event["name"].lower():
                participant_role = discord.utils.get(guild.roles, name="Participant")
                if participant_role and participant_role not in member.roles:
                    try:
                        await member.add_roles(participant_role, reason="Event participation reaction")
                        print(f"Added Participant role to {member.display_name}")
                    except Exception as e:
                        print(f"Failed to add role to {member.display_name}: {e}")
                break

keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
