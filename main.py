import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import re
import random
import time
from collections import defaultdict
from typing import Optional

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

GIVEAWAY_CHANNEL_NAME = "🎁︱𝒩𝓊𝓂𝒷𝑒𝓇-𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎"
ADMIN_ROLES = ["𝓞𝔀𝓷𝓮𝓻 👑", "𓂀 𝒞𝑜-𝒪𝓌𝓃𝑒𝓇 𓂀✅", "Administrator™🌟"]
MESSAGES_PER_SHECKLE = 10  # Number of messages needed to earn 1 sheckle

active_giveaways = {}
user_message_counts = defaultdict(int)
user_inventory = defaultdict(lambda: {"growing": [], "grown": []})
user_sheckles = defaultdict(int)
seeds = {
    "Carrot": (0, 250),
    "Strawberry": (10, 50), # (Sheckles, Messages For quest)
    "Potato": (5, 0),
    "Bamboo": (20, 300),
    "Ember Lily": (55, 550),
    "Sugar Apple": (80, 800),
    "Beanstalk": (70, 750),
}

limited_seeds = {}

trade_offers = {}  # user_id -> dict with keys: sender_id, seed_name, timestamp

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

class GrowingSeed:
    def __init__(self, name, finish_time):
        self.name = name
        self.finish_time = finish_time

def has_admin_role(member):
    return any(role.name in ADMIN_ROLES for role in member.roles)

def update_growing_seeds(user_id):
    """Move finished growing seeds to grown inventory"""
    current_time = time.time()
    growing = user_inventory[user_id]["growing"]
    grown = user_inventory[user_id]["grown"]
    
    # Find all seeds that are done growing
    finished_seeds = [seed for seed in growing if seed.finish_time <= current_time]
    
    # Move them to grown
    for seed in finished_seeds:
        grown.append(seed)
    
    # Remove them from growing
    user_inventory[user_id]["growing"] = [seed for seed in growing if seed.finish_time > current_time]

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        print("Syncing commands...")
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync: {e}")

async def end_giveaway(giveaway):
    if giveaway.task:
        giveaway.task.cancel()

    for winner in giveaway.winners:
        try:
            await winner.send(
                f"🎉 You won the giveaway in {giveaway.channel.mention}!\n**Prize:** {giveaway.prize}\nContact {giveaway.hoster.mention} to claim your reward!"
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
    await giveaway.channel.set_permissions(giveaway.channel.guild.default_role, send_messages=False)
    active_giveaways.pop(giveaway.channel.id, None)

@tree.command(name="add_limited_seed")
@app_commands.describe(name="Seed name", sheckles="Sheckle cost", quest_value="Quest value", duration_minutes="How long it's available")
async def add_limited_seed(interaction: discord.Interaction, name: str, sheckles: int, quest_value: int, duration_minutes: int):
    if not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Not allowed", ephemeral=True)
    limited_seeds[name] = {
        "sheckles": sheckles,
        "quest": quest_value,
        "expires": time.time() + (duration_minutes * 60)
    }
    await interaction.response.send_message(f"✅ Limited seed **{name}** added. Available for {duration_minutes} minutes.")

@tree.command(name="giveaway")
@app_commands.describe(
    winners="Number of winners", prize="Prize", number_range="Range (1-100)",
    hoster="Hoster", duration="Duration in minutes", target="Optional target number"
)
async def start_giveaway(interaction: discord.Interaction, winners: int, prize: str, number_range: str, hoster: discord.Member, duration: int = 0, target: Optional[int] = None):
    await interaction.response.defer()

    if interaction.channel.name != GIVEAWAY_CHANNEL_NAME and not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Use the giveaway channel!", ephemeral=True)
    if interaction.channel.id in active_giveaways:
        return await interaction.followup.send("❌ Giveaway already running here!", ephemeral=True)

    match = re.match(r"(\d+)-(\d+)", number_range)
    if not match:
        return await interaction.followup.send("❌ Invalid range!", ephemeral=True)
    low, high = int(match[1]), int(match[2])
    if low >= high:
        return await interaction.followup.send("❌ Invalid range!", ephemeral=True)

    target = target or random.randint(low, high)
    giveaway = Giveaway(hoster, prize, winners, (low, high), target, duration, interaction.channel)
    active_giveaways[interaction.channel.id] = giveaway

    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.channel.edit(slowmode_delay=2)

    embed = discord.Embed(
        title="🎉 NUMBER GUESS GIVEAWAY",
        description=(
            f"**Host:** {hoster.mention}\n**Range:** {low}-{high}\n**Prize:** {prize}\n"
            f"**Winners:** {winners}\n**Duration:** {'No limit' if duration == 0 else f'{duration} min'}"
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

@tree.command(name="stop_giveaway")
async def stop_giveaway(interaction: discord.Interaction):
    await interaction.response.defer()
    giveaway = active_giveaways.get(interaction.channel.id)
    if not giveaway:
        return await interaction.followup.send("❌ No active giveaway", ephemeral=True)
    if interaction.user.id != giveaway.hoster.id and not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)
    await interaction.followup.send("🛑 Ending giveaway...")
    await end_giveaway(giveaway)

@tree.command(name="inventory")
async def inventory(interaction: discord.Interaction):
    # First update the inventory to move finished seeds
    update_growing_seeds(interaction.user.id)
    
    inv = user_inventory[interaction.user.id]
    grown = ', '.join(p.name for p in inv["grown"]) or 'None'
    growing = ', '.join(f"{p.name} ({int(p.finish_time - time.time())}s left)" for p in inv["growing"]) or 'None'
    sheckles = user_sheckles.get(interaction.user.id, 0)
    
    embed = discord.Embed(title="🌱 Your Garden & Wallet", color=discord.Color.green())
    embed.add_field(name="🌾 Growing", value=growing, inline=False)
    embed.add_field(name="🥕 Grown", value=grown, inline=False)
    embed.add_field(name="💰 Sheckles", value=str(sheckles), inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="sheckles")
async def check_sheckles(interaction: discord.Interaction):
    sheckles = user_sheckles.get(interaction.user.id, 0)
    await interaction.response.send_message(f"💰 You have {sheckles} sheckles.")

@tree.command(name="closest_quest")
async def closest_quest(interaction: discord.Interaction):
    count = user_message_counts[interaction.user.id]
    closest = None
    diff = float('inf')
    for name, (sheck, quest) in seeds.items():
        if quest > 0 and (quest - count) < diff and count < quest:
            closest = (name, quest - count)
            diff = quest - count
    msg = f"Closest quest seed: {closest[0]} ({closest[1]} messages left)" if closest else "You have completed all quests!"
    await interaction.response.send_message(msg)

@tree.command(name="give_seed")
@app_commands.describe(user="User to give seed to", seed="Seed name")
async def give_seed(interaction: discord.Interaction, user: discord.Member, seed: str):
    if not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Not allowed", ephemeral=True)
    grow_time = time.time() + random.randint(300, 600)
    user_inventory[user.id]["growing"].append(GrowingSeed(seed, grow_time))
    await interaction.response.send_message(f"✅ Gave {seed} to {user.mention}")

@tree.command(name="give_sheckles")
@app_commands.describe(user="User to give sheckles to", amount="Amount of sheckles")
async def give_sheckles(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Not allowed", ephemeral=True)
    user_sheckles[user.id] += amount
    await interaction.response.send_message(f"✅ Gave {amount} sheckles to {user.mention}")

@tree.command(name="buy_seed")
@app_commands.describe(seed="Seed name to purchase")
async def buy_seed(interaction: discord.Interaction, seed: str):
    seed = seed.capitalize()
    
    # Check if seed exists in regular shop
    if seed in seeds:
        cost, _ = seeds[seed]
        if cost <= 0:
            return await interaction.response.send_message("❌ This seed is not for sale.", ephemeral=True)
            
        if user_sheckles.get(interaction.user.id, 0) < cost:
            return await interaction.response.send_message("❌ Not enough sheckles!", ephemeral=True)
            
        # Deduct sheckles and add seed
        user_sheckles[interaction.user.id] -= cost
        grow_time = time.time() + random.randint(300, 600)
        user_inventory[interaction.user.id]["growing"].append(GrowingSeed(seed, grow_time))
        return await interaction.response.send_message(f"✅ Purchased {seed} seed for {cost} sheckles! It will be ready in {int(grow_time - time.time())} seconds.")
    
    # Check if seed exists in limited shop
    elif seed in limited_seeds:
        seed_data = limited_seeds[seed]
        if time.time() > seed_data["expires"]:
            return await interaction.response.send_message("❌ This limited seed is no longer available.", ephemeral=True)
            
        if user_sheckles.get(interaction.user.id, 0) < seed_data["sheckles"]:
            return await interaction.response.send_message("❌ Not enough sheckles!", ephemeral=True)
            
        # Check quest requirement
        if seed_data["quest"] > 0 and user_message_counts.get(interaction.user.id, 0) < seed_data["quest"]:
            return await interaction.response.send_message(f"❌ You need to send {seed_data['quest']} messages to unlock this seed!", ephemeral=True)
            
        # Deduct sheckles and add seed
        user_sheckles[interaction.user.id] -= seed_data["sheckles"]
        grow_time = time.time() + random.randint(300, 600)
        user_inventory[interaction.user.id]["growing"].append(GrowingSeed(seed, grow_time))
        return await interaction.response.send_message(f"✅ Purchased limited {seed} seed for {seed_data['sheckles']} sheckles! It will be ready in {int(grow_time - time.time())} seconds.")
    
    # Seed not found
    else:
        await interaction.response.send_message("❌ Seed not found in shop. Use `/shoplist` to see available seeds.", ephemeral=True)

@tree.command(name="trade_offer")
@app_commands.describe(user="User to offer trade to", seed="Seed name to trade")
async def trade_offer(interaction: discord.Interaction, user: discord.Member, seed: str):
    # First update the inventory to move finished seeds
    update_growing_seeds(interaction.user.id)
    
    sender_id = interaction.user.id
    recipient_id = user.id
    seed = seed.capitalize()
    sender_inventory = user_inventory[sender_id]["grown"]

    # Check sender owns the seed
    for grown_seed in sender_inventory:
        if grown_seed.name.lower() == seed.lower():
            # Check if recipient already has a pending trade
            if recipient_id in trade_offers:
                return await interaction.response.send_message("❌ That user already has a pending trade offer.", ephemeral=True)
            # Store the offer with a timestamp
            trade_offers[recipient_id] = {
                "sender_id": sender_id,
                "seed_name": seed,
                "timestamp": time.time()
            }
            await interaction.response.send_message(f"✅ Trade offer sent to {user.mention} for seed {seed}. They can `/trade_accept` or `/trade_decline` within 5 minutes.")
            try:
                await user.send(f"🔔 You have received a trade offer from {interaction.user.mention} for a **{seed}** seed.\n"
                                f"Run `/trade_accept` to accept or `/trade_decline` to decline. Offer expires in 5 minutes.")
            except:
                # Could not DM user, but offer still stands
                pass
            return

    await interaction.response.send_message("❌ You don't have that grown seed to offer.", ephemeral=True)

@tree.command(name="trade_accept")
async def trade_accept(interaction: discord.Interaction):
    # First update the inventory to move finished seeds
    update_growing_seeds(interaction.user.id)
    
    recipient_id = interaction.user.id
    offer = trade_offers.get(recipient_id)
    if not offer:
        return await interaction.response.send_message("❌ You have no pending trade offers.", ephemeral=True)

    sender_id = offer["sender_id"]
    seed = offer["seed_name"]

    # Check offer expiration (5 minutes)
    if time.time() - offer["timestamp"] > 300:
        trade_offers.pop(recipient_id)
        return await interaction.response.send_message("❌ Trade offer expired.", ephemeral=True)

    sender_inventory = user_inventory[sender_id]["grown"]

    # Check sender still has the seed
    for i, grown_seed in enumerate(sender_inventory):
        if grown_seed.name.lower() == seed.lower():
            # Remove from sender and add to recipient
            sender_inventory.pop(i)
            user_inventory[recipient_id]["grown"].append(GrowingSeed(seed, time.time()))
            trade_offers.pop(recipient_id)
            await interaction.response.send_message(f"✅ Trade accepted! You received a {seed} seed from <@{sender_id}>.")
            try:
                sender = await bot.fetch_user(sender_id)
                await sender.send(f"✅ Your trade offer of {seed} to {interaction.user.mention} was accepted.")
            except:
                pass
            return

    # If sender no longer has the seed
    trade_offers.pop(recipient_id)
    await interaction.response.send_message("❌ The sender no longer has the seed to trade.", ephemeral=True)

@tree.command(name="trade_decline")
async def trade_decline(interaction: discord.Interaction):
    recipient_id = interaction.user.id
    offer = trade_offers.pop(recipient_id, None)
    if not offer:
        return await interaction.response.send_message("❌ You have no pending trade offers.", ephemeral=True)
    try:
        sender = await bot.fetch_user(offer["sender_id"])
        await sender.send(f"❌ Your trade offer for {offer['seed_name']} was declined by {interaction.user.mention}.")
    except:
        pass
    await interaction.response.send_message("❌ Trade offer declined.", ephemeral=True)

@tree.command(name="shoplist")
async def shoplist(interaction: discord.Interaction):
    # Regular shop items
    shop_items = [f"{name} - {cost} sheckles" for name, (cost, quest) in seeds.items() if cost > 0]
    
    # Limited time shop items
    current_time = time.time()
    limited_items = []
    for name, data in limited_seeds.items():
        if current_time < data["expires"]:
            quest_req = f" (Requires {data['quest']} messages)" if data["quest"] > 0 else ""
            limited_items.append(f"{name} - {data['sheckles']} sheckles{quest_req} (Limited time!)")
    
    if not shop_items and not limited_items:
        await interaction.response.send_message("🛒 No seeds available for purchase right now.")
        return
    
    embed = discord.Embed(title="🛒 Seed Shop List", color=discord.Color.blue())
    if shop_items:
        embed.add_field(name="Regular Seeds", value="\n".join(shop_items) or "None", inline=False)
    if limited_items:
        embed.add_field(name="Limited Time Seeds", value="\n".join(limited_items) or "None", inline=False)
    
    await interaction.response.send_message(embed=embed)

@tree.command(name="sell_seed")
@app_commands.describe(seed="Seed name to sell", seed_type="Type of seed to sell")
@app_commands.choices(seed_type=[
    app_commands.Choice(name="Growing", value="growing"),
    app_commands.Choice(name="Grown", value="grown")
])
async def sell_seed(interaction: discord.Interaction, seed: str, seed_type: app_commands.Choice[str]):
    # First update the inventory to move finished seeds
    update_growing_seeds(interaction.user.id)
    
    seed = seed.capitalize()
    inv = user_inventory[interaction.user.id]
    
    # Check if user has the seed in specified type
    if seed_type.value == "growing":
        seed_list = [s for s in inv["growing"] if s.name.lower() == seed.lower()]
        if not seed_list:
            return await interaction.response.send_message(
                "❌ You don't have this seed growing. Check your inventory with `/inventory`.",
                ephemeral=True
            )
    else:
        seed_list = [s for s in inv["grown"] if s.name.lower() == seed.lower()]
        if not seed_list:
            return await interaction.response.send_message(
                "❌ You don't have this seed grown. Check your inventory with `/inventory`.",
                ephemeral=True
            )
    
    # Find seed in shop to get base price
    base_price = None
    if seed in seeds:
        base_price = seeds[seed][0]  # Get the purchase price
    elif seed in limited_seeds:
        base_price = limited_seeds[seed]["sheckles"]
    
    if not base_price or base_price <= 0:
        return await interaction.response.send_message(
            "❌ This seed cannot be sold (it has no sale value).",
            ephemeral=True
        )
    
    # Calculate sell price based on type
    if seed_type.value == "growing":
        # Remove first found growing seed
        inv["growing"].remove(seed_list[0])
        sell_price = base_price // 2  # Half price for growing seeds
    else:
        # Remove first found grown seed
        inv["grown"].remove(seed_list[0])
        sell_price = base_price  # Full price for grown seeds
    
    # Add sheckles to user
    user_sheckles[interaction.user.id] += sell_price
    
    await interaction.response.send_message(
        f"✅ Sold {seed} seed ({seed_type.name.lower()}) for {sell_price} sheckles!",
        ephemeral=True
    )

def update_growing_seeds(user_id):
    """Move finished growing seeds to grown inventory"""
    current_time = time.time()
    growing = user_inventory[user_id]["growing"]
    grown = user_inventory[user_id]["grown"]
    
    # Find all seeds that are done growing
    finished_seeds = [seed for seed in growing if seed.finish_time <= current_time]
    
    # Move them to grown
    for seed in finished_seeds:
        grown.append(seed)
    
    # Remove them from growing
    user_inventory[user_id]["growing"] = [seed for seed in growing if seed.finish_time > current_time]

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Increment message count
    user_message_counts[message.author.id] += 1
    
    # Award sheckles for every MESSAGES_PER_SHECKLE messages
    if user_message_counts[message.author.id] % MESSAGES_PER_SHECKLE == 0:
        user_sheckles[message.author.id] += 1

    # Handle giveaway guessing
    giveaway = active_giveaways.get(message.channel.id)
    if giveaway:
        try:
            guess = int(message.content)
        except:
            return
        correct = giveaway.check_guess(message.author, guess)
        if correct is None:
            return
        if correct:
            giveaway.winners.add(message.author)
            if len(giveaway.winners) >= giveaway.winners_required:
                await end_giveaway(giveaway)
        else:
            # optional feedback if you want
            pass

    await bot.process_commands(message)

# Run your bot (replace TOKEN with your bot's token)
bot.run(os.getenv("BOT_TOKEN"))
