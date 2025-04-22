import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import re
import os

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

GIVEAWAY_CHANNEL_NAME = "🎁︱𝒩𝓊𝓂𝒷𝑒𝓇-𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎"
ADMIN_ROLES = ["𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓷𝓮𝓇 𓂀✅", "Administrator™🌟"]

active_giveaways = {}  # {channel_id: Giveaway}

class Giveaway:
    def __init__(self, hoster, prize, winners, number_range, target, duration, channel):
        self.hoster = hoster
        self.prize = prize
        self.winners_required = winners
        self.low, self.high = number_range
        self.target = target
        self.duration = duration
        self.channel = channel
        self.winners = set()
        self.guessed_users = {}
        self.end_time = asyncio.get_event_loop().time() + (duration * 60 if duration > 0 else float('inf'))
        self.task = None

    def check_guess(self, user, guess):
        if user.id == self.hoster.id:
            return None  # Host can't win
        self.guessed_users[user.id] = guess
        return guess == self.target

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        await tree.sync()
        print("Commands synced")
    except Exception as e:
        print("Sync error:", e)

def has_admin_role(member):
    return any(role.name in ADMIN_ROLES for role in member.roles)

async def end_giveaway(giveaway):
    if giveaway.task:
        giveaway.task.cancel()
    
    winners = ", ".join(w.mention for w in giveaway.winners) if giveaway.winners else "No winners"
    embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=f"**Prize:** {giveaway.prize}\n**Target:** {giveaway.target}\n**Winners:** {winners}",
        color=discord.Color.green()
    )
    await giveaway.channel.send(embed=embed)
    
    # Reset channel permissions
    await giveaway.channel.set_permissions(
        giveaway.channel.guild.default_role,
        send_messages=False
    )
    del active_giveaways[giveaway.channel.id]

@tree.command(name="giveaway", description="Start a number guessing giveaway")
@app_commands.describe(
    winners="Number of winners",
    prize="Prize for winners",
    range_="Number range (e.g. 1-100)",
    hoster="Who's hosting",
    duration="Duration in minutes (0=no limit)",
    target="Target number (random if empty)"
)
async def start_giveaway(
    interaction: discord.Interaction,
    winners: int,
    prize: str,
    range_: str,
    hoster: discord.Member,
    duration: int = 0,
    target: int = None
):
    if interaction.channel.name != GIVEAWAY_CHANNEL_NAME and not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Use the giveaway channel!", ephemeral=True)

    if interaction.channel.id in active_giveaways:
        return await interaction.response.send_message("❌ Giveaway already running here!", ephemeral=True)

    # Parse range
    match = re.match(r"(\d+)-(\d+)", range_)
    if not match or int(match[1]) >= int(match[2]):
        return await interaction.response.send_message("❌ Invalid range (use format like 1-100)", ephemeral=True)

    low, high = int(match[1]), int(match[2])
    target = target or random.randint(low, high)

    # Setup giveaway
    giveaway = Giveaway(hoster, prize, winners, (low, high), target, duration, interaction.channel)
    active_giveaways[interaction.channel.id] = giveaway

    # Enable channel with 2-second slowmode
    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        send_messages=True
    )
    await interaction.channel.edit(slowmode_delay=2)  # Discord enforces 2-second cooldown

    # Post giveaway embed
    embed = discord.Embed(
        title="🎉 NUMBER GUESS GIVEAWAY",
        description=(
            f"**Host:** {hoster.mention}\n"
            f"**Range:** {low}-{high}\n"
            f"**Prize:** {prize}\n"
            f"**Winners Needed:** {winners}\n"
            f"**Duration:** {'No limit' if duration == 0 else f'{duration} minutes'}\n\n"
            "**How to Play:**\n"
            "1. Guess a number in the range\n"
            "2. 2-second cooldown between guesses\n"
            "3. Host can't win\n"
            f"4. Use `/stop_giveaway` to end early"
        ),
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed)

    if duration > 0:
        giveaway.task = asyncio.create_task(schedule_giveaway_end(giveaway))

async def schedule_giveaway_end(giveaway):
    await asyncio.sleep(giveaway.duration * 60)
    if giveaway.channel.id in active_giveaways:
        await end_giveaway(giveaway)

@tree.command(name="stop_giveaway", description="End the current giveaway")
async def stop_giveaway(interaction: discord.Interaction):
    giveaway = active_giveaways.get(interaction.channel.id)
    if not giveaway:
        return await interaction.response.send_message("❌ No active giveaway here", ephemeral=True)
    
    if interaction.user.id != giveaway.hoster.id and not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Only the host can stop this", ephemeral=True)

    await interaction.response.send_message("🛑 Ending giveaway...")
    await end_giveaway(giveaway)

@bot.event
async def on_message(message):
    if message.author.bot or message.channel.id not in active_giveaways:
        return

    giveaway = active_giveaways[message.channel.id]

    try:
        guess = int(message.content.strip())
    except ValueError:
        return

    # Let Discord's slowmode handle the 2-second cooldown
    if not (giveaway.low <= guess <= giveaway.high):
        await message.channel.send(f"{message.author.mention} ❌ Out of range!", delete_after=3)
        return

    if giveaway.check_guess(message.author, guess):
        await message.channel.send(f"🎉 {message.author.mention} won!")
        giveaway.winners.add(message.author)
        
        if len(giveaway.winners) >= giveaway.winners_required:
            await end_giveaway(giveaway)

bot.run(os.getenv("BOT_TOKEN"))
