import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import re
import os
from dotenv import load_dotenv
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

GIVEAWAY_CHANNEL_NAME = "🎁︱𝒩𝓊𝓂𝒷𝑒𝓇-𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎"
ADMIN_ROLES = ["𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓇 𓂀✅", "Administrator™🌟"]

active_giveaway = None

class Giveaway:
    def __init__(self, hoster, prize, winners, number_range, target, duration):
        self.hoster = hoster
        self.prize = prize
        self.winners_required = winners
        self.low, self.high = number_range
        self.target = target
        self.duration = duration
        self.winners = set()
        self.guessed_users = {}

    def check_guess(self, user, guess):
        if user.id in self.guessed_users:
            return None  # Already guessed
        self.guessed_users[user.id] = guess
        if guess == self.target:
            self.winners.add(user)
            return True
        return False

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        await tree.sync()
        print("Synced commands")
    except Exception as e:
        print("Sync failed:", e)

def has_admin_role(member):
    return any(role.name in ADMIN_ROLES for role in member.roles)

@tree.command(name="giveaway", description="Start a number guessing giveaway!")
@app_commands.describe(
    winners="Number of winners",
    prize="The prize for the winners",
    range_="Number range (e.g. 1-100)",
    hoster="Hoster of the giveaway",
    duration="Duration (in minutes, optional)",
    target="Target number (optional, leave blank for random)"
)
async def giveaway(
    interaction: discord.Interaction,
    winners: int,
    prize: str,
    range_: str,
    hoster: discord.Member,
    duration: int = 0,
    target: int = None
):
    global active_giveaway

    if interaction.channel.name != GIVEAWAY_CHANNEL_NAME and not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Use this command in the giveaway channel.", ephemeral=True)

    match = re.match(r"(\d+)-(\d+)", range_)
    if not match:
        return await interaction.response.send_message("❌ Invalid range format. Use `1-100` format.", ephemeral=True)

    low, high = int(match[1]), int(match[2])
    if low >= high:
        return await interaction.response.send_message("❌ Invalid range. The first number must be smaller.", ephemeral=True)

    if target is not None and (target < low or target > high):
        return await interaction.response.send_message("❌ Target number must be within the specified range.", ephemeral=True)

    if target is None:
        target = random.randint(low, high)

    active_giveaway = Giveaway(hoster, prize, winners, (low, high), target, duration)

    # Permissions
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.channel.edit(slowmode_delay=5)

    embed = discord.Embed(
        title="🎉 𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎 𝒩𝓊𝓂𝒷𝑒𝓇 𝒢𝒶𝓂𝑒 🎉",
        description=(
            f"**DM:** {hoster.mention}\n"
            f"**Range:** {low}-{high}\n"
            f"**Winners:** {winners}\n"
            f"**Prize:** {prize}\n"
            f"**Duration:** {'No time limit' if duration == 0 else f'{duration} minute(s)'}"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="-- 𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎 𝒩𝓊𝓂𝒷𝑒𝓇 𝒢𝒶𝓂𝑒 --")

    await interaction.response.send_message(embed=embed)

    if duration > 0:
        await asyncio.sleep(duration * 60)
        await end_giveaway(interaction.channel)

@bot.event
async def on_message(message):
    global active_giveaway

    if message.author.bot or active_giveaway is None:
        return

    if message.channel.name != GIVEAWAY_CHANNEL_NAME:
        return

    try:
        guess = int(message.content.strip())
    except ValueError:
        return

    if guess < active_giveaway.low or guess > active_giveaway.high:
        await message.channel.send(f"{message.author.mention} ❌ Guess out of range!", delete_after=5)
        return

    if message.author.id in active_giveaway.guessed_users:
        await message.channel.send(f"{message.author.mention} ⚠️ You already guessed!", delete_after=5)
        return

    correct = active_giveaway.check_guess(message.author, guess)

    if correct:
        await message.channel.send(f"🎉 {message.author.mention} guessed correctly!")
        try:
            await message.author.send(f"🎉 You won the **{active_giveaway.prize}** giveaway!")
        except:
            pass  # Can't DM

        if len(active_giveaway.winners) >= active_giveaway.winners_required:
            await end_giveaway(message.channel)

async def end_giveaway(channel):
    global active_giveaway
    winners = ", ".join(w.mention for w in active_giveaway.winners) if active_giveaway.winners else "No winners"

    await channel.send(f"🔒 Giveaway ended! Winners: {winners}")

    # Lock and reset
    overwrite = channel.overwrites_for(channel.guild.default_role)
    overwrite.send_messages = False
    await channel.set_permissions(channel.guild.default_role, overwrite=overwrite)
    await channel.edit(slowmode_delay=0)
    active_giveaway = None

bot.run(TOKEN)
