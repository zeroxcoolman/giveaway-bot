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
ADMIN_ROLES = ["𝓞𝔀𝓷𝓮𝓻 👑", "Tuff nonchalant aurafarmer sigma pro admin", "Administrator™🌟"]
MESSAGES_PER_SHECKLE = 10  # Number of messages needed to earn 1 sheckle

active_giveaways = {}
user_message_counts = defaultdict(int)
user_inventory = defaultdict(lambda: {"growing": [], "grown": []})
user_sheckles = defaultdict(int)
seeds = {
    "Carrot": (2, 250),
    "Strawberry": (10, 50), # (Sheckles, Messages For quest)
    "Potato": (5, 0),
    "Bamboo": (20, 300),
    "Ember Lily": (55, 550),
    "Sugar Apple": (80, 800),
    "Beanstalk": (70, 750),
}

SEED_RARITIES = {
    "Carrot": "Uncommon",
    "Strawberry": "Common",
    "Potato": "Common",
    "Bamboo": "Rare",
    "Ember Lily": "Mythical",
    "Sugar Apple": "Legendary",
    "Beanstalk": "Legendary",
}

RARITY_CHANCES = {
    "Common": 0.9,
    "Uncommon": 0.6,
    "Rare": 0.35,
    "Mythical": 0.1,
    "Legendary": 0.05,
}

current_stock = []

mutations = {
    "global": {
        "Giant": {
            "multiplier": 1.5,
            "rarity": 0.05,
            "description": "Yields 50% more sheckles"
        },
        "Golden": {
            "multiplier": 2.0,
            "rarity": 0.01,
            "description": "Doubles the sheckle value"
        },
        "Diseased": {
            "multiplier": 0.5,
            "rarity": 0.1,
            "description": "Reduces value by half"
        }
    },
    "specific": {
        "Carrot": {
            "Perfect": {
                "multiplier": 10.0,
                "rarity": 0.001,
                "description": "The ultimate carrot - worth 10x normal value",
                "priority": True
            },
            "Bunny's Favorite": {
                "multiplier": 3.0,
                "rarity": 0.02,
                "description": "Specially loved by rabbits"
            }
        },
        "Ember Lily": {
            "Inferno": {
                "multiplier": 3.5,
                "rarity": 0.03,
                "description": "Burns with eternal flame"
            }
        },
        "Beanstalk": {
            "Skyreach": {
                "multiplier": 4.0,
                "rarity": 0.015,
                "description": "Grows high enough to reach the clouds"
            }
        }
    }
}

limited_seeds = {}

trade_offers = {}  # user_id -> dict with keys: sender_id, seed_name, timestamp
trade_logs = []

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
    def __init__(self, name, finish_time, mutation=None, limited=False, allowed_mutations=None):
        self.name = name
        self.finish_time = finish_time
        self.limited = limited
        self.mutation = mutation or self.determine_mutation(name, allowed_mutations)

    def determine_mutation(self, plant_name, allowed_mutations=None):
        if allowed_mutations is not None:
            # Only consider allowed mutations (either global or specific to this plant)
            pool = []
            for mut in allowed_mutations:
                if mut in mutations["global"]:
                    pool.append((mut, mutations["global"][mut]["rarity"]))
                elif plant_name in mutations["specific"] and mut in mutations["specific"][plant_name]:
                    pool.append((mut, mutations["specific"][plant_name][mut]["rarity"]))

            for mut, rarity in pool:
                if random.random() < rarity:
                    return mut
            return None

        # Normal mutation logic if no restriction
        if plant_name in mutations["specific"]:
            for mut, data in mutations["specific"][plant_name].items():
                if data.get("priority", False) and random.random() < data["rarity"]:
                    return mut
            for mut, data in mutations["specific"][plant_name].items():
                if mut != "Perfect" and random.random() < data["rarity"]:
                    return mut

        for mut, data in mutations["global"].items():
            if random.random() < data["rarity"]:
                return mut

        return None


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
    refresh_stock.start()
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
@app_commands.describe(
    name="Seed name",
    sheckles="Sheckle cost",
    quest_value="Quest value",
    duration_minutes="How long it's available",
    mutations="Optional comma-separated list of possible mutations (leave blank to allow all)"
)
async def add_limited_seed(
    interaction: discord.Interaction,
    name: str,
    sheckles: int,
    quest_value: int,
    duration_minutes: int,
    mutations: str = ""
):
    if not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Not allowed", ephemeral=True)

    normalized_name, _, _ = normalize_seed_name(name)

    # If mutations is blank, allow all mutations (None = unrestricted)
    if not mutations.strip():
        mutation_list = None
    else:
        # Parse and validate mutations
        mutation_list = [m.strip().title() for m in mutations.split(",") if m.strip()]
        
        # Get valid mutations
        valid_mutations = list(mutations_global := mutations["global"].keys())
        if normalized_name in mutations["specific"]:
            valid_mutations += mutations["specific"][normalized_name].keys()

        # Check if any are invalid
        invalid = [m for m in mutation_list if m not in valid_mutations]
        if invalid:
            return await interaction.response.send_message(
                f"❌ Invalid mutation(s): {', '.join(invalid)}", ephemeral=True
            )

    # Save to limited_seeds
    limited_seeds[normalized_name] = {
        "sheckles": sheckles,
        "quest": quest_value,
        "expires": time.time() + (duration_minutes * 60),
        "mutations": mutation_list  # None = allow all, list = restricted
    }

    mut_display = "All mutations allowed" if mutation_list is None else ", ".join(mutation_list)
    await interaction.response.send_message(
        f"✅ Limited seed **{normalized_name}** added for {duration_minutes} minutes.\n"
        f"🔁 Mutations: {mut_display}"
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
    update_growing_seeds(interaction.user.id)
    
    inv = user_inventory[interaction.user.id]
    
    # Format grown seeds with mutations and limited status
    grown_list = []
    for seed in inv["grown"]:
        seed_display = seed.name
        if seed.mutation:
            seed_display += f" ✨({seed.mutation})"
        if getattr(seed, "limited", False):
            seed_display += " 🌟(Limited)"
        grown_list.append(seed_display)
    grown = ', '.join(grown_list) or 'None'
    
    # Format growing seeds with mutations, limited status, and time remaining
    growing_list = []
    for seed in inv["growing"]:
        time_left = int(seed.finish_time - time.time())
        seed_display = seed.name
        if seed.mutation:
            seed_display += f" ✨({seed.mutation})"
        if getattr(seed, "limited", False):
            seed_display += " 🌟(Limited)"
        seed_display += f" [{time_left}s]"
        growing_list.append(seed_display)
    growing = ', '.join(growing_list) or 'None'
    
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

    base, mut, seed = normalize_seed_name(seed)

    if base not in seeds and base not in limited_seeds:
        return await interaction.response.send_message("❌ Invalid seed name.", ephemeral=True)

    grow_time = time.time() + random.randint(300, 600)
    
    # Check if this is a limited seed and set the flag accordingly
    is_limited = base in limited_seeds
    allowed_mutations = limited_seeds[base].get("mutations") if is_limited else None
    
    seed_obj = GrowingSeed(
        base, 
        grow_time, 
        limited=is_limited,  # <-- This is what was missing
        allowed_mutations=allowed_mutations
    )
    
    user_inventory[user.id]["growing"].append(seed_obj)
    await interaction.response.send_message(f"✅ Gave {pretty_seed(seed_obj)} to {user.mention}")

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
    base, mut, seed = normalize_seed_name(seed)  # Normalize input like "ember lily (inferno)"

    # Check if seed exists in stock (regular or limited)
    if base not in current_stock and base not in limited_seeds:
        return await interaction.response.send_message("❌ This seed is not in stock right now!", ephemeral=True)

    # Check if seed is in regular shop
    if base in seeds:
        sheckles_required, _ = seeds[base]
        if sheckles_required <= 0:
            return await interaction.response.send_message("❌ This seed is not for sale.", ephemeral=True)

        if user_sheckles.get(interaction.user.id, 0) < sheckles_required:
            return await interaction.response.send_message("❌ Not enough sheckles!", ephemeral=True)

        # Deduct sheckles and add seed
        user_sheckles[interaction.user.id] -= sheckles_required
        grow_time = time.time() + random.randint(300, 600)
        seed_obj = GrowingSeed(base, grow_time)
        user_inventory[interaction.user.id]["growing"].append(seed_obj)

        return await interaction.response.send_message(
            f"✅ Purchased {pretty_seed(seed_obj)} seed for {sheckles_required} sheckles! "
            f"It will be ready in {int(grow_time - time.time())} seconds."
        )

    # Check if seed is in limited shop
    elif base in limited_seeds:
        seed_data = limited_seeds[base]
        if time.time() > seed_data["expires"]:
            return await interaction.response.send_message("❌ This limited seed is no longer available.", ephemeral=True)

        sheckles_required = seed_data["sheckles"]
        if user_sheckles.get(interaction.user.id, 0) < sheckles_required:
            return await interaction.response.send_message("❌ Not enough sheckles!", ephemeral=True)

        if seed_data["quest"] > 0 and user_message_counts.get(interaction.user.id, 0) < seed_data["quest"]:
            return await interaction.response.send_message(
                f"❌ You need to send {seed_data['quest']} messages to unlock this seed!", ephemeral=True
            )

        # Deduct sheckles and add seed (mark as limited)
        user_sheckles[interaction.user.id] -= sheckles_required
        grow_time = time.time() + random.randint(300, 600)
        allowed_mutations = seed_data.get("mutations", [])

        seed_obj = GrowingSeed(base, grow_time, limited=True, allowed_mutations=allowed_mutations)
        user_inventory[interaction.user.id]["growing"].append(seed_obj)

        return await interaction.response.send_message(
            f"✅ Purchased limited {pretty_seed(seed_obj)} seed for {sheckles_required} sheckles! "
            f"It will be ready in {int(grow_time - time.time())} seconds."
        )

    else:
        await interaction.response.send_message(
            "❌ Seed not found in shop. Use `/shoplist` to see available seeds.", ephemeral=True
        )

def pretty_seed(seed_obj):
    name = f"{seed_obj.name}"
    if seed_obj.mutation:
        name += f" ({seed_obj.mutation})"
    if getattr(seed_obj, "limited", False):
        name += " 🌟(Limited)"
    return name

def normalize_seed_name(raw: str):
    """
    Turns 'ember lily (inferno)' → 'Ember Lily (Inferno)'
    Returns (seed_name, mutation_or_None, combined_string)
    """
    raw = raw.strip()
    m = re.match(r'^(.*?)(?:\((.*?)\))?$', raw)
    base = ' '.join(word.capitalize() for word in m.group(1).split()).strip()
    mut  = m.group(2)
    if mut:
        mut = ' '.join(word.capitalize() for word in mut.split()).strip()
        combined = f"{base} ({mut})"
    else:
        combined = base
    return base, mut, combined

def find_matching_seed(seed_list, desired_input):
    base, mut, _ = normalize_seed_name(desired_input)
    for s in seed_list:
        if s.name != base:
            continue
        if mut is None or (s.mutation and s.mutation.lower() == mut.lower()):
            return s
    return None

@tree.command(name="trade_offer")
@app_commands.describe(user="User to trade with", yourseed="Seed you're offering", theirseed="Seed you want")
async def trade_offer(interaction: discord.Interaction, user: discord.Member, yourseed: str, theirseed: str):
    update_growing_seeds(interaction.user.id)
    update_growing_seeds(user.id)

    sender_id = interaction.user.id
    recipient_id = user.id

    if recipient_id in trade_offers:
        return await interaction.response.send_message("❌ That user already has a pending trade offer.", ephemeral=True)

    sender_seed_obj = find_matching_seed(user_inventory[sender_id]["grown"], yourseed)
    recipient_seed_obj = find_matching_seed(user_inventory[recipient_id]["grown"], theirseed)

    if not sender_seed_obj:
        return await interaction.response.send_message("❌ You don't have that grown seed to offer.", ephemeral=True)
    if not recipient_seed_obj:
        return await interaction.response.send_message(f"❌ {user.mention} doesn't have that seed or it's still growing.", ephemeral=True)

    # Store raw data (not pretty printed) for exact match later
    trade_offers[recipient_id] = {
        "sender_id": sender_id,
        "sender_seed_name": sender_seed_obj.name,
        "sender_seed_mut": sender_seed_obj.mutation,
        "recipient_seed_name": recipient_seed_obj.name,
        "recipient_seed_mut": recipient_seed_obj.mutation,
        "timestamp": time.time()
    }

    await interaction.response.send_message(f"✅ Trade offer sent to {user.mention}.", ephemeral=True)
    try:
        await user.send(f"🔔 You received a trade offer from {interaction.user.mention}:")
        await user.send(f"They offer **{pretty_seed(sender_seed_obj)}** for your **{pretty_seed(recipient_seed_obj)}**.")
        await user.send("Use `/trade_accept @user` or `/trade_decline @user`.")
    except Exception as e:
        print("Failed to DM user about trade:", e)

@tree.command(name="trade_accept")
@app_commands.describe(user="User who sent the trade offer")
async def trade_accept(interaction: discord.Interaction, user: discord.Member):
    update_growing_seeds(interaction.user.id)
    update_growing_seeds(user.id)

    recipient_id = interaction.user.id
    sender_id = user.id

    offer = trade_offers.get(recipient_id)
    if not offer or offer["sender_id"] != sender_id:
        return await interaction.response.send_message("❌ No trade offer from that user.", ephemeral=True)

    # Check expiration
    if time.time() - offer["timestamp"] > 300:
        trade_offers.pop(recipient_id)
        return await interaction.response.send_message("❌ Trade offer expired.", ephemeral=True)

    sender_grown = user_inventory[sender_id]["grown"]
    recipient_grown = user_inventory[recipient_id]["grown"]

    # Find exact matching seeds
    sender_seed = next((s for s in sender_grown if s.name == offer["sender_seed_name"] and s.mutation == offer["sender_seed_mut"]), None)
    recipient_seed = next((s for s in recipient_grown if s.name == offer["recipient_seed_name"] and s.mutation == offer["recipient_seed_mut"]), None)

    if not sender_seed or not recipient_seed:
        trade_offers.pop(recipient_id)
        return await interaction.response.send_message("❌ One or both seeds no longer available.", ephemeral=True)

    # Perform trade (swap objects directly)
    sender_grown.remove(sender_seed)
    recipient_grown.remove(recipient_seed)
    sender_grown.append(recipient_seed)
    recipient_grown.append(sender_seed)

    trade_logs.append({
        "from": sender_id,
        "to": recipient_id,
        "gave": sender_seed.name,
        "got": recipient_seed.name,
        "time": time.time()
    })

    trade_offers.pop(recipient_id)

    received = pretty_seed(sender_seed)
    given = pretty_seed(recipient_seed)

    await interaction.response.send_message(f"✅ Trade complete! You received **{received}** and gave **{given}**.")
    try:
        sender_user = await bot.fetch_user(sender_id)
        await sender_user.send(f"✅ Your trade with {interaction.user.mention} completed! You got **{given}** and gave **{received}**.")
    except:
        pass


@tree.command(name="trade_decline")
@app_commands.describe(user="User who sent the trade offer")
async def trade_decline(interaction: discord.Interaction, user: discord.Member):
    recipient_id = interaction.user.id
    offer = trade_offers.get(recipient_id)
    if not offer or offer["sender_id"] != user.id:
        return await interaction.response.send_message("❌ No trade offer from that user.", ephemeral=True)

    trade_offers.pop(recipient_id)
    await interaction.response.send_message("❌ Trade offer declined.")

    try:
        sender_user = await bot.fetch_user(user.id)
        await sender_user.send(f"❌ Your trade was declined by {interaction.user.mention}.")
    except:
        pass

@tree.command(name="trade_offers")
async def view_trade_offers(interaction: discord.Interaction):
    offer = trade_offers.get(interaction.user.id)
    if not offer:
        return await interaction.response.send_message("📭 You have no pending trade offers.", ephemeral=True)

    sender = await bot.fetch_user(offer["sender_id"])

    # Fetch the real objects from inventory so we can use pretty_seed()
    sender_grown = user_inventory[offer["sender_id"]]["grown"]
    recipient_grown = user_inventory[interaction.user.id]["grown"]
    
    sender_seed = next((s for s in sender_grown if s.name == offer["sender_seed_name"] and s.mutation == offer["sender_seed_mut"]), None)
    recipient_seed = next((s for s in recipient_grown if s.name == offer["recipient_seed_name"] and s.mutation == offer["recipient_seed_mut"]), None)
    
    from_seed = pretty_seed(sender_seed) if sender_seed else offer["sender_seed_name"]
    to_seed = pretty_seed(recipient_seed) if recipient_seed else offer["recipient_seed_name"]

    msg = (
        f"🔁 Pending Trade:\n"
        f"From: {sender.mention}\n"
        f"They offer: {from_seed}\n"
        f"They want: {to_seed}\n"
        f"Use `/trade_accept @{sender.name}` or `/trade_decline @{sender.name}`"
    )

    await interaction.response.send_message(msg, ephemeral=True)

@tree.command(name="trade_logs")
async def trade_logs_command(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
    if not trade_logs:
        return await interaction.response.send_message("📭 No trade logs available.", ephemeral=True)

    embed = discord.Embed(title="📜 Recent Trade Logs", color=discord.Color.gold())
    for log in trade_logs[-10:][::-1]:
        from_user = await bot.fetch_user(log["from"])
        to_user = await bot.fetch_user(log["to"])
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(log["time"]))
        embed.add_field(
            name=f"{from_user.name} ➝ {to_user.name} @ {timestamp}",
            value=f"{from_user.name} gave {log['gave']}, got {log['got']}",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="shoplist")
async def shoplist(interaction: discord.Interaction):
    # PURGE expired seeds FIRST
    global limited_seeds
    limited_seeds = {
        name: data for name, data in limited_seeds.items()
        if time.time() < data["expires"] and data["sheckles"] > 0  # Double-check
    }
    
    embed = discord.Embed(title="🛒 Seed Shop", color=discord.Color.purple())
    
    # Regular stock (unchanged)
    if current_stock:
        embed.add_field(name="🔹 Regular Stock", value="─────────────", inline=False)
        for seed in current_stock:
            cost, _ = seeds[seed]
            rarity = SEED_RARITIES.get(seed, "Unknown")
            embed.add_field(name=seed, value=f"{cost} sheckles\nRarity: {rarity}", inline=True)
    
    # LIMITED SEEDS - Only show if time left > 0 AND valid data
    active_limited = [
        (name, data) for name, data in limited_seeds.items()
        if (time.time() < data["expires"] and 
            data["sheckles"] > 0 and 
            (data.get("quest", 0) >= 0))  # Optional: Check for negative quests
    ]
    
    if active_limited:
        embed.add_field(name="🌟 Limited-Time Seeds", value="─────────────", inline=False)
        for name, data in active_limited:
            time_left = max(0, int((data["expires"] - time.time()) // 60)  # Minutes left
            muts = "All mutations" if data.get("mutations") is None else ", ".join(data["mutations"])
            embed.add_field(
                name=name,
                value=(
                    f"{data['sheckles']} sheckles\n"
                    f"Quest: {data['quest']} messages\n"
                    f"Mutations: {muts}\n"
                    f"⏳ {time_left} minutes left"
                ),
                inline=True
            )
    
    await interaction.response.send_message(embed=embed)

@tree.command(name="sell_seed")
@app_commands.describe(seed="Seed name", seed_type="Seed state")
@app_commands.choices(seed_type=[
    app_commands.Choice(name="Growing", value="growing"),
    app_commands.Choice(name="Grown", value="grown")
])
async def sell_seed(interaction: discord.Interaction, seed: str, seed_type: app_commands.Choice[str]):
    update_growing_seeds(interaction.user.id)
    
    # Normalize input (handles "test (limited)" -> "Test (Limited)")
    base, mut, _ = normalize_seed_name(seed)
    inv = user_inventory[interaction.user.id]
    
    # Dynamic base prices (combine regular + limited seeds)
    base_prices = {
        "Carrot": 2,
        "Strawberry": 10,
        "Potato": 5,
        "Bamboo": 20,
        "Ember Lily": 55,
        "Sugar Apple": 80,
        "Beanstalk": 70,
    }
    
    # Add active limited seeds to pricing
    for seed_name, data in limited_seeds.items():
        if time.time() < data["expires"]:  # Only if not expired
            base_prices[seed_name] = data["sheckles"]  # Use limited seed's sheckle cost as base
    
    # Find seed in inventory
    seed_list = []
    if seed_type.value == "growing":
        seed_list = [s for s in inv["growing"] if s.name.lower() == base.lower()]
    else:
        seed_list = [s for s in inv["grown"] if s.name.lower() == base.lower()]
    
    if not seed_list or base not in base_prices:
        return await interaction.response.send_message("❌ Invalid seed or type", ephemeral=True)
    
    # Calculate price with mutation
    base_price = base_prices[base]
    mult = 1.0
    if seed_list[0].mutation:
        mut = seed_list[0].mutation
        if base in mutations["specific"] and mut in mutations["specific"][base]:
            mult = mutations["specific"][base][mut]["multiplier"]
        elif mut in mutations["global"]:
            mult = mutations["global"][mut]["multiplier"]
    
    price = int(base_price * mult)
    if seed_type.value == "growing":
        price = price // 2  # Half price for growing
    
    # Complete sale
    if seed_type.value == "growing":
        inv["growing"].remove(seed_list[0])
    else:
        inv["grown"].remove(seed_list[0])
    
    user_sheckles[interaction.user.id] += price
    
    msg = f"✅ Sold {seed_list[0].name}"
    if seed_list[0].mutation:
        msg += f" ({seed_list[0].mutation})"
    if getattr(seed_list[0], "limited", False):
        msg += " 🌟(Limited)"
    msg += f" for {price} sheckles!"
    
    await interaction.response.send_message(msg, ephemeral=True)


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

@tasks.loop(minutes=5)
async def refresh_stock():
    global limited_seeds
    limited_seeds = {
        name: data for name, data in limited_seeds.items()
        if time.time() < data["expires"]
    }

    current_stock = []

    # First pass: randomly add seeds based on rarity chance
    for seed, rarity in SEED_RARITIES.items():
        if random.random() < RARITY_CHANCES[rarity]:
            current_stock.append(seed)

    # Ensure at least 2 common seeds are always present
    commons = [s for s, r in SEED_RARITIES.items() if r == "Common"]
    commons_in_stock = [s for s in current_stock if s in commons]
    needed = max(2 - len(commons_in_stock), 0)

    if needed > 0:
        available_commons = list(set(commons) - set(commons_in_stock))
        random.shuffle(available_commons)
        current_stock += available_commons[:needed]

    # Optional: shuffle stock list
    random.shuffle(current_stock)

@tree.command(name="refresh_stock")
async def manual_refresh(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Not allowed", ephemeral=True)
    refresh_stock.restart()
    await interaction.response.send_message("🔁 Stock refreshed!", ephemeral=True)

@tree.command(name="growinstant")
@app_commands.describe(user="User whose plant to instantly grow", plant="Plant name")
async def growinstant(interaction: discord.Interaction, user: discord.Member, plant: str):
    if not has_admin_role(interaction.user):
        return await interaction.response.send_message("❌ Not allowed", ephemeral=True)

    base, mut, _ = normalize_seed_name(plant)
    update_growing_seeds(user.id)

    growing = user_inventory[user.id]["growing"]
    match = next((s for s in growing if s.name == base and (mut is None or s.mutation == mut)), None)

    if not match:
        return await interaction.response.send_message(f"❌ No matching growing seed found for {user.mention}.", ephemeral=True)

    # Preserve the limited status when moving to grown
    grown_seed = GrowingSeed(
        match.name,
        time.time(),  # Instant grow
        mutation=match.mutation,
        limited=getattr(match, "limited", False)  # <-- Preserve limited status
    )
    
    growing.remove(match)
    user_inventory[user.id]["grown"].append(grown_seed)

    await interaction.response.send_message(f"🌱 Instantly grew {pretty_seed(grown_seed)} for {user.mention}.", ephemeral=True)

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
