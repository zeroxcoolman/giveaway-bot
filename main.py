import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import re
import random
import urllib.parse
from playwright.async_api import async_playwright

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# CONFIG
GIVEAWAY_CHANNEL_NAME = "🎁︱𝒩𝓊𝓂𝒷𝑒𝓇-𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎"
QUESTIONS_CHANNEL_NAME = "❓︱questions"
ADMIN_ROLES = ["𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝓮𝓇 𓂀✅", "Administrator™🌟"]
STOCK_CHANNEL_ID = 1383468241560535082
STOCK_URL = "https://vulcanvalues.com/grow-a-garden/stock"

active_giveaways = {}

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
            return None
        self.guessed_users[user.id] = guess
        return guess == self.target

# ----------------- STOCK SCRAPER -----------------
async def fetch_stock():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(STOCK_URL)
            await page.wait_for_timeout(3000)  # wait for JS to load

            sections = await page.locator(".text-white").all()
            stock_text = []

            for section in sections:
                title = await section.inner_text()
                if "STOCK" in title.upper():
                    parent = await section.evaluate_handle("node => node.parentElement")
                    items = await parent.query_selector_all("div:has-text('x')")
                    for item in items:
                        text = await item.inner_text()
                        stock_text.append(text.strip())

            await browser.close()
            return stock_text
    except Exception as e:
        return [f"Error fetching stock: {e}"]

@tasks.loop(minutes=5)
async def post_stock_loop():
    channel = bot.get_channel(STOCK_CHANNEL_ID)
    if not channel:
        print("Stock channel not found.")
        return

    stock_data = await fetch_stock()
    if stock_data:
        content = "**📦 Grow a Garden Stock Update**\n" + "\n".join(f"• {item}" for item in stock_data[:30])
        await channel.send(content)

@tree.command(name="stocknow", description="Get current Grow a Garden stock now")
async def stocknow(interaction: discord.Interaction):
    await interaction.response.defer()
    stock_data = await fetch_stock()
    content = "**📦 Current Stock:**\n" + "\n".join(f"• {item}" for item in stock_data[:30])
    await interaction.followup.send(content)

# ----------------- BOT EVENTS -----------------
@bot.event
async def on_ready():
    print("BROOO I HATE CODE")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    
    try:
        # Sync commands with more detailed error handling
        print("Syncing commands...")
        synced_commands = await tree.sync()
        
        print(f"Successfully synced {len(synced_commands)} commands:")
        for cmd in synced_commands:
            print(f" - {cmd.name}: {cmd.description}")
        
        # Verify with Discord's API
        registered_commands = await bot.tree.fetch_commands()
        print(f"Discord API reports {len(registered_commands)} commands:")
        for cmd in registered_commands:
            print(f" * {cmd.name} - {cmd.description}")
            
            # Debug: Check if stocknow exists in registered commands
            if cmd.name == "stocknow":
                print("   -> /stocknow found in Discord's API!")
                
    except Exception as e:
        print(f"Error syncing commands: {type(e).__name__}: {e}")
        print("Proceeding with potentially outdated commands...")

    # Start your background task
    post_stock_loop.start()
    
def has_admin_role(member):
    return any(role.name in ADMIN_ROLES for role in member.roles)

async def end_giveaway(giveaway):
    if giveaway.task:
        giveaway.task.cancel()
    
    for winner in giveaway.winners:
        try:
            await winner.send(
                f"🎉 You won the giveaway in {giveaway.channel.mention}!\n"
                f"**Prize:** {giveaway.prize}\n"
                f"Contact {giveaway.hoster.mention} to claim your reward!"
            )
        except:
            pass
    
    winners_text = ", ".join(w.mention for w in giveaway.winners) if giveaway.winners else "No winners"
    embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=f"**Prize:** {giveaway.prize}\n**Target:** {giveaway.target}\n**Winners:** {winners_text}",
        color=discord.Color.green()
    )
    await giveaway.channel.send(embed=embed)
    
    await giveaway.channel.set_permissions(
        giveaway.channel.guild.default_role,
        send_messages=False
    )
    if giveaway.channel.id in active_giveaways:
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
async def start_giveaway(interaction: discord.Interaction, winners: int, prize: str, range_: str,
                        hoster: discord.Member, duration: int = 0, target: int = None):
    await interaction.response.defer()
    
    if interaction.channel.name != GIVEAWAY_CHANNEL_NAME and not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Use the giveaway channel!", ephemeral=True)

    if interaction.channel.id in active_giveaways:
        return await interaction.followup.send("❌ Giveaway already running here!", ephemeral=True)

    match = re.match(r"(\d+)-(\d+)", range_)
    if not match or int(match[1]) >= int(match[2]):
        return await interaction.followup.send("❌ Invalid range (use format like 1-100)", ephemeral=True)

    low, high = int(match[1]), int(match[2])
    target = target or random.randint(low, high)

    giveaway = Giveaway(hoster, prize, winners, (low, high), target, duration, interaction.channel)
    active_giveaways[interaction.channel.id] = giveaway

    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        send_messages=True
    )
    await interaction.channel.edit(slowmode_delay=2)

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
            f"4. Use `/stop_giveaway` or DM me 'stop' to end early\n\n"
            "Winners will receive prize details via DM!"
        ),
        color=discord.Color.gold()
    )
    await interaction.followup.send(embed=embed)

    if duration > 0:
        giveaway.task = asyncio.create_task(schedule_giveaway_end(giveaway))

async def schedule_giveaway_end(giveaway):
    await asyncio.sleep(giveaway.duration * 60)
    if giveaway.channel.id in active_giveaways:
        await end_giveaway(giveaway)

@tree.command(name="stop_giveaway", description="End the current giveaway")
async def stop_giveaway(interaction: discord.Interaction):
    await interaction.response.defer()
    
    giveaway = active_giveaways.get(interaction.channel.id)
    if not giveaway:
        return await interaction.followup.send("❌ No active giveaway here", ephemeral=True)
    
    if interaction.user.id != giveaway.hoster.id and not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Only the host can stop this", ephemeral=True)

    await interaction.followup.send("🛑 Ending giveaway...")
    await end_giveaway(giveaway)

@tree.command(name="guiderods", description="Get the Fishing Progression Guide")
async def guiderods(interaction: discord.Interaction):
    await interaction.response.defer()
    
    if interaction.channel.name != QUESTIONS_CHANNEL_NAME:
        return await interaction.followup.send(
            f"❌ This command can only be used in #{QUESTIONS_CHANNEL_NAME}!",
            ephemeral=True
        )
    
    embed = discord.Embed(
        title="🎣 Fishing Rod Progression Guide",
        color=discord.Color.blue()
    )
    embed.set_image(url="https://fischipedia.org/wiki/Special:FilePath/Progress_Tiers.png")
    await interaction.followup.send(embed=embed)

@tree.command(name="searchrod", description="Search for a rod on Fischipedia")
@app_commands.describe(rod_name="Name of the rod to search for")
async def searchrod(interaction: discord.Interaction, rod_name: str):
    await interaction.response.defer()
    
    if interaction.channel.name != QUESTIONS_CHANNEL_NAME:
        return await interaction.followup.send(
            f"❌ This command can only be used in #{QUESTIONS_CHANNEL_NAME}!",
            ephemeral=True
        )
    
    safe_name = urllib.parse.quote(rod_name)
    await interaction.followup.send(
        f"🔍 Search results for '{rod_name}':\n"
        f"https://fischipedia.org/w/index.php?search={safe_name}&title=Special:Search&go=Go"
    )

@bot.event
async def on_message(message):
    if isinstance(message.channel, discord.DMChannel):
        for giveaway in list(active_giveaways.values()):
            if message.author.id == giveaway.hoster.id and message.content.lower() in ["stop", "end", "cancel"]:
                await message.channel.send("🛑 Stopping your giveaway...")
                await end_giveaway(giveaway)
        return

    if message.author.bot or message.channel.id not in active_giveaways:
        return

    giveaway = active_giveaways[message.channel.id]

    try:
        guess = int(message.content.strip())
    except ValueError:
        return

    if not (giveaway.low <= guess <= giveaway.high):
        await message.channel.send(f"{message.author.mention} ❌ Must be between {giveaway.low}-{giveaway.high}!", delete_after=3)
        return

    result = giveaway.check_guess(message.author, guess)
    if result is None:
        await message.channel.send(f"{message.author.mention} ❌ Hosts can't win!", delete_after=3)
    elif result:
        await message.channel.send(f"🎉 {message.author.mention} guessed correctly!")
        giveaway.winners.add(message.author)
        
        try:
            await message.author.send(
                f"🎊 You guessed the number in {giveaway.channel.mention}!\n"
                f"**Prize:** {giveaway.prize}\n"
                f"The host {giveaway.hoster.mention} will contact you soon."
            )
        except:
            await message.channel.send(f"{message.author.mention} Couldn't DM you - enable DMs to receive your prize info!", delete_after=10)
        
        if len(giveaway.winners) >= giveaway.winners_required:
            await end_giveaway(giveaway)

bot.run(os.getenv("BOT_TOKEN"))
