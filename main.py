import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import re
import os
from discord.app_commands import checks
from discord.ext.commands import CooldownMapping

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
giveaway_cooldown = CooldownMapping.from_cooldown(1, 2.0, commands.BucketType.user)  # 1 guess per 2 seconds per user

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
        # Prevent hoster from winning
        if user.id == self.hoster.id:
            return None
            
        self.guessed_users[user.id] = guess
        
        if guess == self.target:
            self.winners.add(user)
            return True
        return False

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print("Sync failed:", e)

def has_admin_role(member):
    return any(role.name in ADMIN_ROLES for role in member.roles)

async def end_giveaway(giveaway):
    global active_giveaways
    
    if giveaway.task and not giveaway.task.done():
        giveaway.task.cancel()
    
    winners = ", ".join(w.mention for w in giveaway.winners) if giveaway.winners else "No winners"

    embed = discord.Embed(
        title="🎉 Giveaway Ended!",
        description=f"**Prize:** {giveaway.prize}\n**Target Number:** {giveaway.target}\n**Winners:** {winners}",
        color=discord.Color.green()
    )
    await giveaway.channel.send(embed=embed)

    # Lock channel
    overwrite = giveaway.channel.overwrites_for(giveaway.channel.guild.default_role)
    overwrite.send_messages = False
    await giveaway.channel.set_permissions(giveaway.channel.guild.default_role, overwrite=overwrite)
    
    if giveaway.channel.id in active_giveaways:
        del active_giveaways[giveaway.channel.id]

@tree.command(name="giveaway", description="Start a number guessing giveaway!")
@app_commands.describe(
    winners="Number of winners",
    prize="The prize for the winners",
    range_="Number range (e.g. 1-100)",
    hoster="Hoster of the giveaway",
    duration="Duration (in minutes, optional)",
    target="Target number (optional, leave blank for random)"
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
    global active_giveaways

    if interaction.channel.name != GIVEAWAY_CHANNEL_NAME and not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Use this command in the giveaway channel.", ephemeral=True)

    if interaction.channel.id in active_giveaways:
        return await interaction.response.send_message("❌ There's already an active giveaway in this channel!", ephemeral=True)

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

    giveaway = Giveaway(hoster, prize, winners, (low, high), target, duration, interaction.channel)
    active_giveaways[interaction.channel.id] = giveaway

    # Permissions
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    await interaction.channel.edit(slowmode_delay=0)  # No slowmode, using Discord's cooldown system

    embed = discord.Embed(
        title="🎉 𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎 𝒩𝓊𝓂𝒷𝑒𝓇 𝒢𝒶𝓂𝑒 🎉",
        description=(
            f"**Hosted by:** {hoster.mention}\n"
            f"**Range:** {low}-{high}\n"
            f"**Winners:** {winners}\n"
            f"**Prize:** {prize}\n"
            f"**Duration:** {'No time limit' if duration == 0 else f'{duration} minute(s)'}\n\n"
            f"**Rules:**\n"
            f"- Guess a number between {low} and {high}\n"
            f"- 2 second cooldown between guesses\n"
            f"- Host cannot win\n"
            f"- Unlimited guesses!\n\n"
            f"Use `/stop_giveaway` or DM the host to end early"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="-- 𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎 𝒩𝓊𝓂𝒷𝑒𝓇 𝒢𝒶𝓂𝑒 --")

    await interaction.response.send_message(embed=embed)

    if duration > 0:
        giveaway.task = asyncio.create_task(schedule_giveaway_end(giveaway))

async def schedule_giveaway_end(giveaway):
    try:
        await asyncio.sleep(giveaway.duration * 60)
        if giveaway.channel.id in active_giveaways:
            await end_giveaway(giveaway)
    except asyncio.CancelledError:
        pass

@tree.command(name="stop_giveaway", description="Stop the current giveaway in this channel")
async def stop_giveaway(interaction: discord.Interaction):
    if interaction.channel.id not in active_giveaways:
        return await interaction.response.send_message("❌ No active giveaway in this channel!", ephemeral=True)

    giveaway = active_giveaways[interaction.channel.id]
    
    if interaction.user.id != giveaway.hoster.id and not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Only the giveaway hoster can stop this!", ephemeral=True)

    await interaction.response.send_message("🛑 Giveaway is being stopped...")
    await end_giveaway(giveaway)

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Handle DM to hoster to stop giveaway
    if isinstance(message.channel, discord.DMChannel):
        for giveaway in active_giveaways.values():
            if message.author.id == giveaway.hoster.id and message.content.lower() in ["stop", "end", "cancel"]:
                await message.channel.send("🛑 Giveaway is being stopped...")
                await end_giveaway(giveaway)
                return
        return

    # Handle guesses in giveaway channels
    if message.channel.id not in active_giveaways:
        return

    # Discord-managed cooldown check
    bucket = giveaway_cooldown.get_bucket(message)
    if bucket.update_rate_limit():
        await message.channel.send(f"{message.author.mention} ⏳ Please wait 2 seconds between guesses!", delete_after=2)
        return

    giveaway = active_giveaways[message.channel.id]

    try:
        guess = int(message.content.strip())
    except ValueError:
        return

    if guess < giveaway.low or guess > giveaway.high:
        await message.channel.send(f"{message.author.mention} ❌ Guess out of range!", delete_after=5)
        return

    result = giveaway.check_guess(message.author, guess)
    
    if result is None:  # Hoster tried to guess
        await message.channel.send(f"{message.author.mention} ❌ Host cannot participate!", delete_after=5)
        return
    elif result:  # Correct guess
        await message.channel.send(f"🎉 {message.author.mention} guessed correctly!")
        try:
            await message.author.send(f"🎉 You won the **{giveaway.prize}** giveaway!")
        except:
            pass  # Can't DM

        if len(giveaway.winners) >= giveaway.winners_required:
            await end_giveaway(giveaway)

bot.run(os.getenv("BOT_TOKEN"))
